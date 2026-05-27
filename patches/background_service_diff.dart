// ──────────────────────────────────────────────────────────────────────────
// background_service.dart — TWO CHANGES NEEDED
// ──────────────────────────────────────────────────────────────────────────

// ─── CHANGE 1 of 2 ────────────────────────────────────────────────────────
// Location: after line 71 (after kNativeLocationBootstrapServiceOwnedFixKey)
//
// Add this constant with the other SharedPreferences key declarations:

const String kNativePositionUpdatedMsKey = 'native_position_updated_ms';

// ─── CHANGE 2 of 2 ────────────────────────────────────────────────────────
// Location: in getBestEffortPosition(), after "await bootstrapPrefs.reload();"
// (currently line 791) and before the nativeBootstrapActive declaration.
//
// Add this block — it is Tier 0, checked before all existing bootstrap logic:

    // ── Tier 0: Live native GPS (persistent requestLocationUpdates feed) ──
    // NativeLocationBootstrap writes KEY_UPDATED_MS on every fix (~2s interval).
    // If the timestamp is fresh (<10s), the bootstrap_location_* keys hold a
    // live, service-owned position — consume it immediately and skip all Dart
    // GPS acquisition paths. This bypasses OxygenOS background GPS restriction
    // because the native foreground service has higher-priority hardware access.
    final nativeLiveMs = bootstrapPrefs.getInt(kNativePositionUpdatedMsKey) ?? 0;
    if (nativeLiveMs > 0) {
      final nativeLiveAge = DateTime.now().millisecondsSinceEpoch - nativeLiveMs;
      if (nativeLiveAge < 10000) {
        final liveSample = await readNativeBootstrapPosition();
        if (liveSample != null) {
          consecutiveLocationTimeouts = 0;
          if (useFastColdStartAttempt) btStartupFastAttemptsRemaining = 0;
          lastPositionAt = DateTime.now();
          lastSuccessfulPositionAt = lastPositionAt;
          _diag(
            'Tier0: live native GPS (age=${nativeLiveAge}ms '
            'accuracy=${liveSample.position.accuracy.toStringAsFixed(1)}m '
            'speed=${liveSample.position.speed.toStringAsFixed(1)}mps)',
          );
          return liveSample.position;
        }
      }
    }

// ──────────────────────────────────────────────────────────────────────────
// After these two changes, the complete getBestEffortPosition() preamble
// (lines 777–791 plus the new block) looks like this:
// ──────────────────────────────────────────────────────────────────────────

  Future<Position?> getBestEffortPosition() async {
    final inBtStartupWindow = btStartupGraceUntil != null &&
        DateTime.now().isBefore(btStartupGraceUntil!);
    final useFastColdStartAttempt = inBtStartupWindow &&
        btStartupFastAttemptsRemaining > 0 &&
        lastSuccessfulPositionAt == null;
    final attemptTimeout = useFastColdStartAttempt
        ? kColdStartGpsAttemptTimeout
        : kNormalGpsAttemptTimeout;

    final bootstrapPrefs = await SharedPreferences.getInstance();
    await bootstrapPrefs.reload();

    // ── Tier 0: Live native GPS (persistent requestLocationUpdates feed) ──
    final nativeLiveMs = bootstrapPrefs.getInt(kNativePositionUpdatedMsKey) ?? 0;
    if (nativeLiveMs > 0) {
      final nativeLiveAge = DateTime.now().millisecondsSinceEpoch - nativeLiveMs;
      if (nativeLiveAge < 10000) {
        final liveSample = await readNativeBootstrapPosition();
        if (liveSample != null) {
          consecutiveLocationTimeouts = 0;
          if (useFastColdStartAttempt) btStartupFastAttemptsRemaining = 0;
          lastPositionAt = DateTime.now();
          lastSuccessfulPositionAt = lastPositionAt;
          _diag(
            'Tier0: live native GPS (age=${nativeLiveAge}ms '
            'accuracy=${liveSample.position.accuracy.toStringAsFixed(1)}m '
            'speed=${liveSample.position.speed.toStringAsFixed(1)}mps)',
          );
          return liveSample.position;
        }
      }
    }

    // ... rest of getBestEffortPosition() unchanged (nativeBootstrapActive check, etc.)

// ──────────────────────────────────────────────────────────────────────────
// OPTIONAL CLEANUP (not blocking, but tidy):
//
// warmGeolocatorPluginBridge() (lines 182–196) calls getServiceStatusStream()
// which is the wrong stream type and always times out after 2s on every cold
// start. It does nothing useful. Remove the function and the call at line 196.
// ──────────────────────────────────────────────────────────────────────────
