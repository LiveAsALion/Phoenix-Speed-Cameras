# SpeedShield Cold-Start GPS Fix — New Session Handoff

## What you are doing and why

SpeedShield is a Flutter+Kotlin Android app that announces speed camera locations
via TTS while driving. The core use case is cold-start, off-screen,
Bluetooth-triggered operation.

**The bug:** Every cold off-screen camera pass produces zero alerts. Two field
tests confirmed this across builds. The app works fine when opened manually.

**Root cause (confirmed by field test log analysis):**
OxygenOS aggressively restricts GPS hardware access from Flutter background
isolates, even with `foregroundServiceType.location` declared. The Flutter
background service (`flutter_background_service`) runs in a separate Dart VM.
`FusedLocationProviderClient` calls from that isolate return
`Location availability=false` and produce consecutive GPS timeouts (251+
observed over 2+ hours with zero recovery). This is an OEM hardware restriction,
not a channel binding issue.

The native Android foreground service has unrestricted GPS hardware access.
`NativeLocationBootstrap.kt` already uses `FusedLocationProviderClient` and
writes positions to SharedPreferences. The fix is to keep it running for the
entire vehicle session instead of stopping after the first fix.

---

## The three changes needed

### Change 1 — NativeLocationBootstrap.kt (MOST IMPORTANT, VERIFY CAREFULLY)

**Goal:** Convert from one-shot bootstrapper to persistent provider.

**Current behaviour:** Gets one position (via `getCurrentLocation()` or similar),
writes it to SharedPreferences, marks ready, stops.

**Required behaviour:** Call `requestLocationUpdates()` on session start. Keep the
`LocationCallback` alive for the entire vehicle session. Write every fix to
SharedPreferences. Write `native_position_updated_ms` (Long, milliseconds) on
each update so Dart knows the data is live. Only stop on `NativeLocationBootstrap.stop()`.

**Specific changes to make:**
- Replace the one-shot acquisition with `fusedClient.requestLocationUpdates()`
  using `Priority.PRIORITY_HIGH_ACCURACY`, 2000ms interval, 1000ms min interval
- In `onLocationResult`: write all position fields AND
  `putLong("flutter.native_position_updated_ms", System.currentTimeMillis())`
- In `onLocationAvailability`: do NOT stop when `isLocationAvailable=false` —
  cold GPS hardware needs time to warm up, the subscription keeps it powered
- In `stop()`: call `fusedClient.removeLocationUpdates(callback)` and write
  `putLong("flutter.native_position_updated_ms", 0L)`
- Keep all existing SharedPreferences keys exactly as they are
- Keep the existing `storeLocation()` method structure if it exists — add the
  `native_position_updated_ms` write to it or call it from `onLocationResult`

**Key facts confirmed:**
- Existing file uses `putString` for lat/lon/accuracy/speed/bearing (matches
  Flutter's `setDouble` convention — no format migration needed)
- PREFS_NAME = "FlutterSharedPreferences" (from MonitoringService.kt)
- All keys have "flutter." prefix

**DO NOT blindly replace with the patch file** at `patches/NativeLocationBootstrap.kt`.
Read the actual current file first. The patch was written without seeing the
existing implementation. Apply the conceptual changes above to the actual file
so you preserve any existing structure, methods, or callers you find.

### Change 2 — MonitoringService.kt

**Goal:** Start native GPS before Flutter engine boots.

**Single addition** in the `ACTION_START` handler, before `handler.removeCallbacks`:
```kotlin
NativeLocationBootstrap.start(applicationContext, "monitoring_start")
```

The `ACTION_STOP` handler already calls `NativeLocationBootstrap.stop()`.
No other changes needed. The patch at `patches/MonitoringService.kt` is correct
for this change — verify the `ACTION_START` block looks the same as the uploaded
original before applying.

### Change 3 — background_service.dart (lib/services/background_service.dart)

**Two precise edits. Both are correct — the patch was made against the actual file.**

**Edit A** — add constant after `kNativeLocationBootstrapServiceOwnedFixKey`:
```dart
const String kNativePositionUpdatedMsKey = 'native_position_updated_ms';
```

**Edit B** — add Tier-0 block in `getBestEffortPosition()`, immediately after
`await bootstrapPrefs.reload()` and before the `nativeBootstrapActive` declaration:
```dart
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
        'Tier0: live native GPS age=${nativeLiveAge}ms '
        'accuracy=${liveSample.position.accuracy.toStringAsFixed(1)}m '
        'speed=${liveSample.position.speed.toStringAsFixed(1)}mps',
      );
      return liveSample.position;
    }
  }
}
```

The complete modified `background_service.dart` is at `patches/background_service.dart`
in this repo (phoenix-speed-cameras, branch claude/review-speedshield-diagnostics-CEM8d)
if you want to diff against it.

---

## How to verify correctness before pushing

1. Read `NativeLocationBootstrap.kt` from the SpeedShield repo
2. Grep for all callers: `grep -rn "NativeLocationBootstrap" android/`
3. Confirm `stop()` is the only method called from outside the class (besides the
   new `start()` you're adding in MonitoringService)
4. Confirm the existing `storeLocation()` (or equivalent) writes using `putString`
5. Apply changes to the actual file as a diff, not a replacement
6. Read `background_service.dart` from the SpeedShield repo and confirm the
   constant block and `getBestEffortPosition()` signature match what's in the patch

---

## What success looks like in the diagnostic log

- `"Persistent location updates started (monitoring_start)"` — native layer running
- `"Tier0: live native GPS age=Xms"` — Dart consuming native positions
- No more `"GPS acquisition timed out"` accumulation during vehicle session

## Branch to push to

Push changes to a new branch in the SpeedShield repo, e.g.
`fix/persistent-native-gps-cold-start`. Do not push to main.
