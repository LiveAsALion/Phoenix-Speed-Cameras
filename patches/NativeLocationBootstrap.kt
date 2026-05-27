package com.speedshield.app

import android.content.Context
import android.os.Looper
import android.util.Log
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.LocationAvailability
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority

/**
 * Persistent native GPS provider for the duration of a vehicle session.
 *
 * Replaces the previous one-shot bootstrapper design. Calls requestLocationUpdates()
 * on FusedLocationProviderClient and writes each fix to SharedPreferences so the
 * Flutter background isolate can consume live positions without relying on the
 * Geolocator plugin channel (which OxygenOS starves in background isolates).
 *
 * start() — called on vehicle BT connect (before Flutter engine initialises)
 * stop()  — called on monitoring stop / BT disconnect
 */
object NativeLocationBootstrap {

    private const val TAG = "NativeLocationBootstrap"
    private const val PREFS_NAME = "FlutterSharedPreferences"

    @Volatile private var fusedClient: FusedLocationProviderClient? = null
    @Volatile private var locationCallback: LocationCallback? = null
    @Volatile private var isRunning = false

    // ── SharedPreferences keys (written by Kotlin, read by Dart) ─────────────
    // Dart reads these with prefs.getDouble() / prefs.getInt() / prefs.getBool()
    // Flutter's Android plugin stores doubles as Strings and ints as Longs.
    private const val KEY_LAT             = "flutter.bootstrap_location_lat"
    private const val KEY_LON             = "flutter.bootstrap_location_lon"
    private const val KEY_ACCURACY        = "flutter.bootstrap_location_accuracy"
    private const val KEY_SPEED           = "flutter.bootstrap_location_speed"
    private const val KEY_BEARING         = "flutter.bootstrap_location_bearing"
    private const val KEY_TIME_MS         = "flutter.bootstrap_location_time_ms"
    private const val KEY_SOURCE          = "flutter.bootstrap_location_source"
    private const val KEY_ACTIVE          = "flutter.native_location_bootstrap_active"
    private const val KEY_READY           = "flutter.native_location_bootstrap_ready"
    private const val KEY_TIMED_OUT       = "flutter.native_location_bootstrap_timed_out"
    private const val KEY_SERVICE_OWNED   = "flutter.native_location_bootstrap_service_owned_fix"
    private const val KEY_STARTED_MS      = "flutter.native_location_bootstrap_started_ms"
    // New key — millisecond timestamp of the most recent native write.
    // Dart's Tier-0 in getBestEffortPosition() checks this to detect "live" data.
    private const val KEY_UPDATED_MS      = "flutter.native_position_updated_ms"

    fun start(context: Context, reason: String) {
        if (isRunning) {
            Log.d(TAG, "Already running; ignoring duplicate start ($reason)")
            DiagnosticLogger.log(context, TAG, "Already running; ignoring duplicate start ($reason)")
            return
        }

        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit()
            .putBoolean(KEY_ACTIVE,        true)
            .putBoolean(KEY_READY,         false)
            .putBoolean(KEY_TIMED_OUT,     false)
            .putBoolean(KEY_SERVICE_OWNED, false)
            .putLong(KEY_STARTED_MS,       System.currentTimeMillis())
            .putLong(KEY_UPDATED_MS,       0L)
            .commit()

        val client = LocationServices.getFusedLocationProviderClient(context)
        fusedClient = client

        val cb = object : LocationCallback() {
            override fun onLocationResult(result: LocationResult) {
                val location = result.lastLocation ?: return
                val now = System.currentTimeMillis()

                // Flutter's shared_preferences stores doubles as String on Android.
                prefs.edit()
                    .putString(KEY_LAT,           location.latitude.toString())
                    .putString(KEY_LON,           location.longitude.toString())
                    .putString(KEY_ACCURACY,      location.accuracy.toString())
                    .putString(KEY_SPEED,         location.speed.toString())
                    .putString(KEY_BEARING,       location.bearing.toString())
                    .putLong(KEY_TIME_MS,         location.time)
                    .putString(KEY_SOURCE,        "persistent_native")
                    .putBoolean(KEY_READY,        true)
                    .putBoolean(KEY_SERVICE_OWNED,true)
                    .putLong(KEY_UPDATED_MS,      now)
                    .commit()

                DiagnosticLogger.log(
                    context, TAG,
                    "Position update: lat=${location.latitude} lon=${location.longitude} " +
                    "accuracy=${location.accuracy}m speed=${location.speed}mps " +
                    "bearing=${location.bearing}deg age=${now - location.time}ms"
                )
            }

            override fun onLocationAvailability(availability: LocationAvailability) {
                DiagnosticLogger.log(
                    context, TAG,
                    "Location availability=${availability.isLocationAvailable}"
                )
                // Do NOT stop on unavailable — cold GPS hardware will warm up.
                // The persistent subscription keeps the hardware powered.
            }
        }
        locationCallback = cb

        try {
            val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 2000L)
                .setMinUpdateIntervalMillis(1000L)
                .build()
            client.requestLocationUpdates(request, cb, Looper.getMainLooper())
            isRunning = true
            Log.d(TAG, "Persistent location updates started ($reason)")
            DiagnosticLogger.log(context, TAG, "Persistent location updates started ($reason)")
        } catch (e: SecurityException) {
            Log.e(TAG, "Location permission denied: ${e.message}")
            DiagnosticLogger.log(context, TAG, "Location permission denied: ${e.message}")
            locationCallback = null
            fusedClient = null
            prefs.edit()
                .putBoolean(KEY_TIMED_OUT, true)
                .putBoolean(KEY_ACTIVE,    false)
                .commit()
        }
    }

    fun stop(context: Context, reason: String) {
        val cb = locationCallback
        val client = fusedClient
        if (cb != null && client != null) {
            try {
                client.removeLocationUpdates(cb)
            } catch (e: Exception) {
                Log.w(TAG, "removeLocationUpdates failed: ${e.message}")
            }
        }
        locationCallback = null
        fusedClient = null
        isRunning = false

        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit()
            .putBoolean(KEY_ACTIVE,     false)
            .putLong(KEY_UPDATED_MS,    0L)
            .commit()

        Log.d(TAG, "Stopped ($reason)")
        DiagnosticLogger.log(context, TAG, "Stopped ($reason)")
    }
}
