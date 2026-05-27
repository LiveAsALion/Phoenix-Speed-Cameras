// ──────────────────────────────────────────────────────────────────────────
// MonitoringService.kt — ONE CHANGE NEEDED
//
// In the ACTION_START handler (around line 82–94), add a NativeLocationBootstrap.start()
// call BEFORE scheduling the Flutter service.
//
// This ensures native GPS begins acquiring before the Flutter engine even initialises
// (~1–3s cold start). By the time runLoop() calls getBestEffortPosition() for the
// first time, the native layer has already been asking the hardware for a fix.
// ──────────────────────────────────────────────────────────────────────────

// BEFORE (existing code):
ACTION_START -> {
    handler.removeCallbacks(startRunnable)
    handler.postDelayed(startRunnable, START_DEBOUNCE_MS)
    DiagnosticLogger.log(applicationContext, TAG, "Scheduled ACTION_START after ${START_DEBOUNCE_MS}ms debounce")
    return START_NOT_STICKY
}

// AFTER (add the NativeLocationBootstrap.start call):
ACTION_START -> {
    NativeLocationBootstrap.start(applicationContext, "monitoring_start")   // ← ADD THIS LINE
    handler.removeCallbacks(startRunnable)
    handler.postDelayed(startRunnable, START_DEBOUNCE_MS)
    DiagnosticLogger.log(applicationContext, TAG, "Scheduled ACTION_START after ${START_DEBOUNCE_MS}ms debounce")
    return START_NOT_STICKY
}

// ──────────────────────────────────────────────────────────────────────────
// That's the only change to MonitoringService.kt.
// ACTION_STOP already calls NativeLocationBootstrap.stop() at line 117.
// ──────────────────────────────────────────────────────────────────────────
