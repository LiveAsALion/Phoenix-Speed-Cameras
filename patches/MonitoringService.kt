package com.speedshield.app

import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import android.content.Context

/// Thin bridge service — receives BT/Activity triggers and starts/stops
/// the Flutter background service. Runs for <1 second, not persistent.
///
/// Uses plain startService (NOT startForegroundService) so Android doesn't
/// require startForeground() and won't show "keeps stopping" if it exits quickly.
///
/// Includes a 1-second debounce on START so rapid BT disconnect/reconnect
/// cycles (common during Android Auto link negotiation) don't hammer the
/// Flutter service while a prior isolate is still tearing down.
class MonitoringService : Service() {
    companion object {
        const val TAG = "MonitoringService"
        const val ACTION_START = "com.speedshield.app.START_MONITORING"
        const val ACTION_STOP = "com.speedshield.app.STOP_MONITORING"
        const val ACTION_BT_CONNECTED = "com.speedshield.app.BT_CONNECTED"
        const val ACTION_BT_DISCONNECTED = "com.speedshield.app.BT_DISCONNECTED"
        const val ACTION_AA_CONNECTED = "com.speedshield.app.AA_CONNECTED"
        const val ACTION_AA_DISCONNECTED = "com.speedshield.app.AA_DISCONNECTED"
        const val ACTION_ACTIVITY_IN_VEHICLE = "com.speedshield.app.ACTIVITY_IN_VEHICLE"
        const val ACTION_ACTIVITY_NOT_IN_VEHICLE = "com.speedshield.app.ACTIVITY_NOT_IN_VEHICLE"
        private const val FLUTTER_BG_SERVICE = "com.speedshield.app.SpeedShieldBackgroundService"
        private const val START_DEBOUNCE_MS = 1_000L
        private const val EVENT_START_DEBOUNCE_MS = 0L
        private const val PREFS_NAME = "FlutterSharedPreferences"
        private const val KEY_PENDING_EVENT_ACTION = "flutter.pending_event_action"
        private const val KEY_PENDING_EVENT_AT_MS = "flutter.pending_event_at_ms"
        private const val KEY_PENDING_EVENT_DEVICE_NAME = "flutter.pending_event_device_name"
        private const val KEY_PENDING_EVENT_DEVICE_ADDRESS = "flutter.pending_event_device_address"
        private const val KEY_PENDING_EVENT_ACTIVITY_CONFIDENCE = "flutter.pending_event_activity_confidence"
        private const val KEY_PENDING_EVENT_SOURCE = "flutter.pending_event_source"
    }

    private val handler = Handler(Looper.getMainLooper())
    private val startRunnable = Runnable {
        Log.d(TAG, "Starting Flutter background service (debounced)")
        DiagnosticLogger.log(applicationContext, TAG, "Starting Flutter background service (debounced)")
        try {
            val bgIntent = Intent(applicationContext, Class.forName(FLUTTER_BG_SERVICE))
            // Use plain startService — NOT startForegroundService.
            // flutter_background_service.BackgroundService promotes itself to foreground
            // via startForeground() internally once the Flutter engine initializes.
            // Using startForegroundService imposes a hard OS deadline (5s stock, ~30s OxygenOS)
            // for startForeground() to be called — Flutter cold-start can exceed this → crash.
            applicationContext.startService(bgIntent)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start Flutter service: ${e.message}")
            DiagnosticLogger.log(applicationContext, TAG, "Failed to start Flutter service: ${e.message}")
        }
        // Stop self AFTER launching the Flutter service — not before.
        // Calling stopSelf() before the runnable fires triggers onDestroy(),
        // which removes the pending callbacks, so the Flutter service never starts.
        stopSelf()
    }

    private fun persistPendingEvent(intent: Intent?) {
        val action = intent?.action ?: return
        if (action == ACTION_START || action == ACTION_STOP) return
        val prefs = applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit()
            .putString(KEY_PENDING_EVENT_ACTION, action)
            .putLong(KEY_PENDING_EVENT_AT_MS, intent.getLongExtra("event_at_ms", System.currentTimeMillis()))
            .putString(KEY_PENDING_EVENT_DEVICE_NAME, intent.getStringExtra("device_name") ?: "")
            .putString(KEY_PENDING_EVENT_DEVICE_ADDRESS, intent.getStringExtra("device_address") ?: "")
            .putInt(KEY_PENDING_EVENT_ACTIVITY_CONFIDENCE, intent.getIntExtra("activity_confidence", -1))
            .putString(KEY_PENDING_EVENT_SOURCE, intent.getStringExtra("source") ?: "")
            .apply()
        DiagnosticLogger.log(applicationContext, TAG, "Persisted one-time pending event handoff action=$action")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.d(TAG, "onStartCommand: action=${intent?.action}")
        DiagnosticLogger.log(applicationContext, TAG, "onStartCommand action=${intent?.action}")

        when (intent?.action) {
            ACTION_START -> {
                // Start native GPS immediately — before the Flutter engine boots.
                // The persistent LocationCallback begins warming the GPS hardware
                // while Flutter cold-starts (~1–3s), so the first Dart loop tick
                // already has a live native position waiting in SharedPreferences.
                NativeLocationBootstrap.start(applicationContext, "monitoring_start")
                // Cancel any in-flight start and reschedule — absorbs rapid
                // connect/disconnect bursts from Android Auto BT negotiation.
                // DO NOT call stopSelf() here — it triggers onDestroy() which
                // removes pending callbacks before the runnable fires.
                handler.removeCallbacks(startRunnable)
                handler.postDelayed(startRunnable, START_DEBOUNCE_MS)
                DiagnosticLogger.log(applicationContext, TAG, "Scheduled ACTION_START after ${START_DEBOUNCE_MS}ms debounce")
                return START_NOT_STICKY
            }
            ACTION_BT_CONNECTED,
            ACTION_BT_DISCONNECTED,
            ACTION_AA_CONNECTED,
            ACTION_AA_DISCONNECTED,
            ACTION_ACTIVITY_IN_VEHICLE,
            ACTION_ACTIVITY_NOT_IN_VEHICLE -> {
                persistPendingEvent(intent)
                handler.removeCallbacks(startRunnable)
                if (EVENT_START_DEBOUNCE_MS <= 0L) {
                    handler.post(startRunnable)
                } else {
                    handler.postDelayed(startRunnable, EVENT_START_DEBOUNCE_MS)
                }
                DiagnosticLogger.log(applicationContext, TAG, "Scheduled runtime wake for pending event ${intent.action} after ${EVENT_START_DEBOUNCE_MS}ms debounce")
                return START_NOT_STICKY
            }
            ACTION_STOP -> {
                // Cancel a pending start if stop arrives before the debounce fires
                handler.removeCallbacks(startRunnable)
                Log.d(TAG, "Stopping Flutter background service")
                DiagnosticLogger.log(applicationContext, TAG, "Stopping Flutter background service")
                NativeLocationBootstrap.stop(applicationContext, "monitoring_stop")
                try {
                    val bgIntent = Intent(applicationContext, Class.forName(FLUTTER_BG_SERVICE))
                    applicationContext.stopService(bgIntent)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to stop Flutter service: ${e.message}")
                    DiagnosticLogger.log(applicationContext, TAG, "Failed to stop Flutter service: ${e.message}")
                }
            }
        }

        stopSelf()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacks(startRunnable)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
