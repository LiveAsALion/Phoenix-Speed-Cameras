import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:ui';
import 'package:flutter/widgets.dart';
import 'package:flutter_background_service/flutter_background_service.dart';
import 'package:geolocator/geolocator.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/alert_settings.dart';
import '../models/camera.dart';
import 'camera_service.dart';
import 'proximity_service.dart';
import 'alert_service.dart';
import 'settings_service.dart';
import 'vehicle_detection_service.dart';
import 'diagnostic_log_service.dart';
import 'background_runtime_policy.dart';

/// SharedPreferences key written by MapScreen to signal it is in the foreground.
/// Background service skips TTS when this flag is true to prevent double-alerting.
const String kAppForegroundKey = 'app_in_foreground';
const String kRawBtConnectedKey = 'raw_bt_connected';
const String kRawBtDeviceNameKey = 'raw_bt_device_name';
const String kRawBtDeviceAddressKey = 'raw_bt_device_address';
const String kRawBtEventAtMsKey = 'raw_bt_event_at_ms';
const String kRawAaConnectedKey = 'raw_aa_connected';
const String kRawAaEventAtMsKey = 'raw_aa_event_at_ms';
const String kRawActivityInVehicleKey = 'raw_activity_in_vehicle';
const String kRawActivityConfidenceKey = 'raw_activity_confidence';
const String kRawActivityEventAtMsKey = 'raw_activity_event_at_ms';
const String kMonitoringActiveKey = 'monitoring_active';
const String kCurrentVehicleConnectedKey = 'current_vehicle_connected';
const String kCurrentVehicleNameKey = 'current_vehicle_name';
const String kCurrentVehicleAddressKey = 'current_vehicle_address';
const String kAlertEngineOwnerKey = 'alert_engine_owner';
const String kLatestPositionTimestampMsKey = 'latest_position_timestamp_ms';
const String kLatestPositionLatKey = 'latest_position_lat';
const String kLatestPositionLonKey = 'latest_position_lon';
const String kLatestPositionAccuracyKey = 'latest_position_accuracy';
const String kPendingEventActionKey = 'pending_event_action';
const String kPendingEventAtMsKey = 'pending_event_at_ms';
const String kPendingEventDeviceNameKey = 'pending_event_device_name';
const String kPendingEventDeviceAddressKey = 'pending_event_device_address';
const String kPendingEventActivityConfidenceKey =
    'pending_event_activity_confidence';
const String kPendingEventSourceKey = 'pending_event_source';
const String kAcquisitionStateKey = 'acquisition_state';
// TEMPORARY diagnostic logging for battery / dormant-mode verification.
// Remove or disable before public release.
const bool kTempDiagnosticLogging = true;
const String kLastAlertStateKey = 'last_alert_state';
const String kUiHeartbeatMsKey = 'ui_heartbeat_ms';
const String kBootstrapLocationLatKey = 'bootstrap_location_lat';
const String kBootstrapLocationLonKey = 'bootstrap_location_lon';
const String kBootstrapLocationAccuracyKey = 'bootstrap_location_accuracy';
const String kBootstrapLocationSpeedKey = 'bootstrap_location_speed';
const String kBootstrapLocationBearingKey = 'bootstrap_location_bearing';
const String kBootstrapLocationTimeMsKey = 'bootstrap_location_time_ms';
const String kBootstrapLocationSourceKey = 'bootstrap_location_source';
const String kNativeLocationBootstrapActiveKey =
    'native_location_bootstrap_active';
const String kNativeLocationBootstrapReadyKey =
    'native_location_bootstrap_ready';
const String kNativeLocationBootstrapReadySourceKey =
    'native_location_bootstrap_ready_source';
const String kNativeLocationBootstrapReadyTimeMsKey =
    'native_location_bootstrap_ready_time_ms';
const String kNativeLocationBootstrapTimedOutKey =
    'native_location_bootstrap_timed_out';
const String kNativeLocationBootstrapServiceOwnedFixKey =
    'native_location_bootstrap_service_owned_fix';
const String kNativePositionUpdatedMsKey = 'native_position_updated_ms';
const Duration kNativeBootstrapPositionMaxAge = Duration(seconds: 45);
const double kNativeBootstrapPositionMaxAccuracyMeters = 120.0;
const Duration kNativeBootstrapReadyWait = Duration(seconds: 12);
const double kNativeBootstrapFallbackAccuracyMeters = 90.0;
const Duration kNativeBootstrapFallbackMaxAge = Duration(seconds: 20);
const Duration kForegroundGraceWindow = Duration(seconds: 3);
const Duration kAlertDedupeWindow = Duration(seconds: 20);
const Duration kBtStartupGpsGraceWindow = Duration(minutes: 3);
const Duration kBtSignalDropGraceWindow = Duration(seconds: 45);
const Duration kColdStartGpsAttemptTimeout = Duration(seconds: 6);
const Duration kNormalGpsAttemptTimeout = Duration(seconds: 12);
const Duration kReadinessStreamAttemptTimeout = Duration(seconds: 4);
const Duration kReadinessStreamMaxAge = Duration(seconds: 12);
const double kReadinessStreamMaxAccuracyMeters = 120.0;
const Duration kBootstrapPositionMaxAge = Duration(minutes: 5);
const double kFallbackWalkingPaceMps = 1.4;
const double kFallbackMaxEstimatedDriftMeters = 150.0;
const double kFallbackAbsoluteMaxSpeedMps = 44.0;
const double kBootstrapPositionMaxAccuracyMeters = 120.0;
const Duration kColdStartNoFixShutdownGrace = Duration(minutes: 5);
const int kColdStartGpsFastAttempts = 8;
final DiagnosticLogService _diagnostics = DiagnosticLogService.shared;

enum AcquisitionState { dormant, acquiring, monitoring, teardownGrace }

void _diag(String message) {
  if (kTempDiagnosticLogging) {
    debugPrint('[SpeedShield][TEMP] $message');
  }
  unawaited(_diagnostics.log('background_service', message));
}

Future<void> initBackgroundService() async {
  await FlutterBackgroundService().configure(
    androidConfiguration: AndroidConfiguration(
      onStart: onServiceStart,
      autoStart: false,
      isForegroundMode: true,
      notificationChannelId: 'speedshield_service_v2',
      initialNotificationTitle: 'SpeedShield',
      initialNotificationContent: 'Monitoring for speed cameras...',
      foregroundServiceNotificationId: 888,
      foregroundServiceTypes: [AndroidForegroundType.location],
    ),
    iosConfiguration: IosConfiguration(
      autoStart: false,
      onForeground: onServiceStart,
      onBackground: onIosBackground,
    ),
  );
}

Future<void> startBackgroundService() async {
  final service = FlutterBackgroundService();
  final running = await service.isRunning();
  if (!running) await service.startService();
}

@pragma('vm:entry-point')
Future<bool> onIosBackground(ServiceInstance service) async {
  WidgetsFlutterBinding.ensureInitialized();
  DartPluginRegistrant.ensureInitialized();
  AlertService.markBackgroundAudioBridgeReady();
  return true;
}

List<String> _loadApprovedAddresses(SharedPreferences prefs) {
  try {
    // NOTE: Flutter's SharedPreferences plugin automatically prepends 'flutter.'
    // when reading/writing. Do NOT include it manually — doing so causes Dart
    // to look for 'flutter.flutter.vehicleBluetoothDevices', which never exists.
    // SettingsService.save() writes this as 'vehicleBluetoothDevices' (stored
    // natively as 'flutter.vehicleBluetoothDevices', readable by Kotlin directly).
    final raw = prefs.getString('vehicleBluetoothDevices');
    if (raw == null || raw.isEmpty) return [];
    final list = jsonDecode(raw) as List<dynamic>;
    return list
        .map((e) => (e as Map<String, dynamic>)['address'] as String? ?? '')
        .where((a) => a.isNotEmpty)
        .toList();
  } catch (_) {
    return [];
  }
}

@pragma('vm:entry-point')
void onServiceStart(ServiceInstance service) async {
  DartPluginRegistrant.ensureInitialized();
  AlertService.markBackgroundAudioBridgeReady();
  _diag('Background service entrypoint started');

  if (service is AndroidServiceInstance) {
    service
        .on('setAsForeground')
        .listen((_) => service.setAsForegroundService());
    service
        .on('setAsBackground')
        .listen((_) => service.setAsBackgroundService());
  }
  service.on('stopService').listen((_) => service.stopSelf());

  final cameraService = CameraService();
  final proximityService = ProximityService();
  final alertService = AlertService();
  final settingsService = SettingsService();
  final vehicleDetectionService = VehicleDetectionService();

  var cameras = await cameraService.getAllCameras();
  var settings = await settingsService.load();

  Future<void> warmGeolocatorPluginBridge() async {
    try {
      await Geolocator.getServiceStatusStream()
          .first
          .timeout(const Duration(seconds: 2));
      _diag('Background Geolocator service-status bridge warmed successfully');
    } on TimeoutException {
      _diag(
          'Background Geolocator service-status warmup timed out; continuing with normal GPS acquisition');
    } catch (e) {
      _diag('Background Geolocator service-status warmup failed: $e');
    }
  }

  await warmGeolocatorPluginBridge();

  Future<void> persistRuntimeSnapshot({
    required bool monitoringActive,
    required bool vehicleSessionActive,
    required DateTime? sessionStartTime,
    required AcquisitionState acquisitionState,
    required bool currentVehicleConnected,
    required String currentVehicleName,
    required String currentVehicleAddress,
    required Position? lastPosition,
    String alertEngineOwner = 'background_service',
  }) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(kMonitoringActiveKey, monitoringActive);
    await prefs.setBool('vehicle_session_active', vehicleSessionActive);
    await prefs.setInt(
      'vehicle_session_start_ms',
      vehicleSessionActive
          ? (sessionStartTime ?? DateTime.now()).millisecondsSinceEpoch
          : 0,
    );
    await prefs.setBool(kCurrentVehicleConnectedKey, currentVehicleConnected);
    await prefs.setString(kAcquisitionStateKey, acquisitionState.name);
    await prefs.setString(kCurrentVehicleNameKey, currentVehicleName);
    await prefs.setString(kCurrentVehicleAddressKey, currentVehicleAddress);
    await prefs.setString(kAlertEngineOwnerKey, alertEngineOwner);
    await prefs.setInt(
        'service_heartbeat_ms', DateTime.now().millisecondsSinceEpoch);
    if (lastPosition != null) {
      final timestampMs = lastPosition.timestamp.millisecondsSinceEpoch;
      await prefs.setInt(kLatestPositionTimestampMsKey, timestampMs);
      await prefs.setDouble(kLatestPositionLatKey, lastPosition.latitude);
      await prefs.setDouble(kLatestPositionLonKey, lastPosition.longitude);
      await prefs.setDouble(kLatestPositionAccuracyKey, lastPosition.accuracy);
    }
  }

  Future<void> consumePendingNativeEvent() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    final action = prefs.getString(kPendingEventActionKey) ?? '';
    if (action.isEmpty) return;
    final eventAtMs = prefs.getInt(kPendingEventAtMsKey) ??
        DateTime.now().millisecondsSinceEpoch;
    final deviceName = prefs.getString(kPendingEventDeviceNameKey) ?? '';
    final deviceAddress = prefs.getString(kPendingEventDeviceAddressKey) ?? '';
    final activityConfidence =
        prefs.getInt(kPendingEventActivityConfidenceKey) ?? -1;
    final source = prefs.getString(kPendingEventSourceKey) ?? '';

    switch (action) {
      case 'com.speedshield.app.BT_CONNECTED':
        await prefs.setBool(kRawBtConnectedKey, true);
        await prefs.setString(kRawBtDeviceNameKey, deviceName);
        await prefs.setString(kRawBtDeviceAddressKey, deviceAddress);
        await prefs.setInt(kRawBtEventAtMsKey, eventAtMs);
        await prefs.setBool('bt_connected', true);
        await prefs.setString('bt_device_name', deviceName);
        await prefs.setString('bt_device_address', deviceAddress);
        await prefs.setBool(kCurrentVehicleConnectedKey, true);
        await prefs.setString(kCurrentVehicleNameKey, deviceName);
        await prefs.setString(kCurrentVehicleAddressKey, deviceAddress);
        _diag('Consumed pending BT_CONNECTED handoff from $source');
        break;
      case 'com.speedshield.app.BT_DISCONNECTED':
        await prefs.setBool(kRawBtConnectedKey, false);
        await prefs.setInt(kRawBtEventAtMsKey, eventAtMs);
        await prefs.setBool('bt_connected', false);
        await prefs.setBool(kCurrentVehicleConnectedKey, false);
        _diag('Consumed pending BT_DISCONNECTED handoff from $source');
        break;
      case 'com.speedshield.app.AA_CONNECTED':
        await prefs.setBool(kRawAaConnectedKey, true);
        await prefs.setInt(kRawAaEventAtMsKey, eventAtMs);
        await prefs.setBool('aa_connected', true);
        _diag('Consumed pending AA_CONNECTED handoff from $source');
        break;
      case 'com.speedshield.app.AA_DISCONNECTED':
        await prefs.setBool(kRawAaConnectedKey, false);
        await prefs.setInt(kRawAaEventAtMsKey, eventAtMs);
        await prefs.setBool('aa_connected', false);
        _diag('Consumed pending AA_DISCONNECTED handoff from $source');
        break;
      case 'com.speedshield.app.ACTIVITY_IN_VEHICLE':
        await prefs.setBool(kRawActivityInVehicleKey, true);
        await prefs.setInt(kRawActivityConfidenceKey, activityConfidence);
        await prefs.setInt(kRawActivityEventAtMsKey, eventAtMs);
        _diag('Consumed pending ACTIVITY_IN_VEHICLE handoff from $source');
        break;
      case 'com.speedshield.app.ACTIVITY_NOT_IN_VEHICLE':
        await prefs.setBool(kRawActivityInVehicleKey, false);
        await prefs.setInt(kRawActivityConfidenceKey, activityConfidence);
        await prefs.setInt(kRawActivityEventAtMsKey, eventAtMs);
        _diag('Consumed pending ACTIVITY_NOT_IN_VEHICLE handoff from $source');
        break;
    }

    await prefs.remove(kPendingEventActionKey);
    await prefs.remove(kPendingEventAtMsKey);
    await prefs.remove(kPendingEventDeviceNameKey);
    await prefs.remove(kPendingEventDeviceAddressKey);
    await prefs.remove(kPendingEventActivityConfidenceKey);
    await prefs.remove(kPendingEventSourceKey);
  }

  await consumePendingNativeEvent();

  Future<void> clearAlertDispatchState() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(kLastAlertStateKey);
  }

  Future<bool> shouldDispatchAlert(
    String tier,
    ProximityResult alert,
    bool appInForeground, {
    required double currentSpeedMps,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();

    final now = DateTime.now();
    final raw = prefs.getString(kLastAlertStateKey);
    Map<String, dynamic> state = {};
    if (raw != null && raw.isNotEmpty) {
      try {
        state = jsonDecode(raw) as Map<String, dynamic>;
      } catch (_) {
        state = {};
      }
    }

    final cameraKey =
        '$tier:${alert.camera.latitude},${alert.camera.longitude},${alert.camera.directionDeg}';
    final entry = state[cameraKey] is Map<String, dynamic>
        ? state[cameraKey] as Map<String, dynamic>
        : <String, dynamic>{};

    final lastEpochMs = (entry['at'] as num?)?.toInt() ?? 0;
    final lastDistance = (entry['distance'] as num?)?.toDouble();
    final lastSource = entry['source'] as String? ?? '';
    final lastForeground = entry['foreground'] as bool?;

    if (lastEpochMs > 0) {
      final elapsed =
          now.difference(DateTime.fromMillisecondsSinceEpoch(lastEpochMs));
      final distanceDelta = lastDistance == null
          ? double.infinity
          : (alert.distanceMeters - lastDistance).abs();
      final sameForegroundState = lastForeground == appInForeground;
      final likelyDuplicate = elapsed <= kAlertDedupeWindow &&
          distanceDelta <= (tier == 'primary' ? 75.0 : 35.0) &&
          (sameForegroundState || lastSource == 'background');
      if (likelyDuplicate) {
        _diag(
            'Suppressing duplicate $tier alert for ${alert.camera.name} at ${alert.distanceMeters.toStringAsFixed(0)}m (elapsed=${elapsed.inSeconds}s lastSource=$lastSource foreground=$appInForeground)');
        return false;
      }
      if (shouldSuppressStationaryReAlert(
        currentSpeedMps: currentSpeedMps,
        currentDistanceMeters: alert.distanceMeters,
        lastDistanceMeters: lastDistance,
        elapsedSinceLastAlert: elapsed,
      )) {
        _diag(
            'Suppressing stationary re-alert for $tier ${alert.camera.name} at ${alert.distanceMeters.toStringAsFixed(0)}m (speed=${currentSpeedMps.toStringAsFixed(1)}mps lastDistance=${lastDistance?.toStringAsFixed(0)}m elapsed=${elapsed.inSeconds}s)');
        return false;
      }
    }

    state[cameraKey] = {
      'at': now.millisecondsSinceEpoch,
      'distance': alert.distanceMeters,
      'source': appInForeground ? 'foreground' : 'background',
      'foreground': appInForeground,
    };
    await prefs.setString(kLastAlertStateKey, jsonEncode(state));
    return true;
  }

  Future<bool> isForegroundRecentlyReleased() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    final appInForeground = prefs.getBool(kAppForegroundKey) ?? false;
    if (appInForeground) return true;

    final uiHeartbeatMs = prefs.getInt(kUiHeartbeatMsKey) ?? 0;
    if (uiHeartbeatMs <= 0) return false;
    final heartbeatAt = DateTime.fromMillisecondsSinceEpoch(uiHeartbeatMs);
    final age = DateTime.now().difference(heartbeatAt);
    if (age.isNegative) {
      _diag(
          'UI heartbeat timestamp is in the future; ignoring recent-foreground gating');
      return false;
    }
    final recent = age <= kForegroundGraceWindow;
    if (recent) {
      _diag(
          'UI heartbeat still fresh (${age.inMilliseconds}ms); treating app as recently foregrounded');
    }
    return recent;
  }

  DateTime? autoDetectSpeedSince;
  bool monitoringEnabled = settings.activationMode == ActivationMode.alwaysOn;
  AcquisitionState acquisitionState = monitoringEnabled
      ? AcquisitionState.monitoring
      : AcquisitionState.dormant;
  DateTime? btStartupGraceUntil;
  int btStartupFastAttemptsRemaining = 0;

  // Adaptive GPS polling — interval scales with distance to nearest camera.
  // Far (>1000m): 8s | Mid (250–1000m): 2s | Close (<250m): 1s
  // When no vehicle session is active (BT mode), always uses 8s safe default.
  Position? lastPosition;
  DateTime? lastPositionAt;
  int consecutiveLocationTimeouts = 0;
  DateTime? lastSuccessfulPositionAt;

  // Vehicle session state — persists through Android Auto's BT→WiFi Direct handoff.
  //
  // Session lifecycle:
  //   START  → approved BT device connects (vehicleConnectionStream fires true)
  //   ACTIVE → BT connected OR aa_connected=true (AA WiFi Direct session live)
  //   END    → both BT and AA gone → session ends and native triggers own the next wake
  //
  // This means: train tracks, red lights, drive-throughs = session stays active
  // (AA is still connected). Only truly parks when the car is off and AA disconnects.
  bool vehicleSessionActive = false;
  DateTime? sessionStartTime; // tracks when current vehicle session began
  // Safety valve: if aa_connected stays true for more than 4 hours, force-reset.
  // Prevents the overnight stuck-session bug where AA content provider never
  // clears projection_status after WiFi is turned off externally.
  const int maxSessionHours = 4;

  Future<void> clearPersistedVehicleSessionState(
      {String reason = 'stale_restore_rejected'}) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    await prefs.setBool('vehicle_session_active', false);
    await prefs.setInt('vehicle_session_start_ms', 0);
    await prefs.setBool(kMonitoringActiveKey, false);
    await prefs.setString(kAcquisitionStateKey, AcquisitionState.dormant.name);
    await prefs.setBool(kCurrentVehicleConnectedKey, false);
    await prefs.setString(kCurrentVehicleNameKey, '');
    await prefs.setString(kCurrentVehicleAddressKey, '');
    await prefs.setString(kAlertEngineOwnerKey, 'background_service');
    await prefs.setBool('aa_connected', false);
    await prefs.setBool(kRawAaConnectedKey, false);
    await prefs.setBool('bt_connected', false);
    await prefs.setBool(kRawBtConnectedKey, false);
    _diag('Cleared persisted vehicle session state ($reason)');
  }

  // Restore persisted session state across service restarts.
  // WatchdogWorker can restart this service mid-session; without persisted state,
  // in-memory variables reset and the 4-hour safety valve loses its clock.
  {
    final initPrefs = await SharedPreferences.getInstance();
    await initPrefs.reload();
    final persistedSessionActive =
        initPrefs.getBool('vehicle_session_active') ?? false;
    final persistedSessionStartMs =
        initPrefs.getInt('vehicle_session_start_ms') ?? 0;
    final shouldRestore = shouldRestoreVehicleSession(
      persistedSessionActive: persistedSessionActive,
      persistedSessionStartMs: persistedSessionStartMs,
      rawBtConnected: initPrefs.getBool(kRawBtConnectedKey) ??
          initPrefs.getBool('bt_connected') ??
          false,
      rawAaConnected: initPrefs.getBool(kRawAaConnectedKey) ??
          initPrefs.getBool('aa_connected') ??
          false,
      rawBtEventAtMs: initPrefs.getInt(kRawBtEventAtMsKey) ?? 0,
      rawAaEventAtMs: initPrefs.getInt(kRawAaEventAtMsKey) ?? 0,
      now: DateTime.now(),
    );
    if (shouldRestore) {
      vehicleSessionActive = true;
      sessionStartTime =
          DateTime.fromMillisecondsSinceEpoch(persistedSessionStartMs);
      monitoringEnabled = true;
      acquisitionState = AcquisitionState.acquiring;
      _diag(
          'Restored active session from SharedPrefs: started $sessionStartTime mode=${settings.activationMode.name}');
    } else if (persistedSessionActive || persistedSessionStartMs > 0) {
      await clearPersistedVehicleSessionState();
    }
  }

  // Clear stale aa_connected if no active session was restored.
  // aa_connected can be left as true from a prior session if the AA content
  // provider didn't fire a disconnect event (e.g., WiFi turned off externally).
  // Only trust aa_connected if we also restored an active vehicle session.
  if (!vehicleSessionActive) {
    final aaPrefs = await SharedPreferences.getInstance();
    final staleAa = aaPrefs.getBool('aa_connected') ?? false;
    if (staleAa) {
      await aaPrefs.setBool('aa_connected', false);
      await aaPrefs.setBool(kRawAaConnectedKey, false);
      await aaPrefs.setBool(kCurrentVehicleConnectedKey, false);
      await aaPrefs.setString(kCurrentVehicleNameKey, '');
      await aaPrefs.setString(kCurrentVehicleAddressKey, '');
      _diag('Cleared stale aa_connected on service start (no active session)');
    }
  }

  if (settings.activationMode == ActivationMode.vehicleBluetooth) {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    vehicleDetectionService.approvedAddresses = _loadApprovedAddresses(prefs);
    await vehicleDetectionService.start();
    vehicleDetectionService.vehicleConnectionStream.listen((connected) async {
      if (connected) {
        // Approved BT vehicle connected — start or re-affirm session.
        // Reset proximity alert state so every new drive starts clean.
        proximityService.resetAlertState();
        await alertService
            .prewarmAudio(); // warm BT audio stream before first alert
        vehicleSessionActive = true;
        sessionStartTime = DateTime.now();
        btStartupGraceUntil = DateTime.now().add(kBtStartupGpsGraceWindow);
        btStartupFastAttemptsRemaining = kColdStartGpsFastAttempts;
        await clearAlertDispatchState();
        monitoringEnabled = true;
        acquisitionState = AcquisitionState.acquiring;
        await persistRuntimeSnapshot(
          monitoringActive: true,
          vehicleSessionActive: true,
          sessionStartTime: sessionStartTime,
          acquisitionState: acquisitionState,
          currentVehicleConnected: true,
          currentVehicleName: prefs.getString(kCurrentVehicleNameKey) ??
              prefs.getString('bt_device_name') ??
              '',
          currentVehicleAddress: prefs.getString(kCurrentVehicleAddressKey) ??
              prefs.getString('bt_device_address') ??
              '',
          lastPosition: lastPosition,
        );
        _diag('Vehicle-BT session started at $sessionStartTime');
      }
      // On BT disconnect: do NOT immediately kill monitoring.
      // If AA WiFi Direct is active (aa_connected=true), the drive continues.
      // The GPS timer evaluates the full signal picture on every tick.
      if (service is AndroidServiceInstance) {
        final aaActive = vehicleDetectionService.isAndroidAutoActive;
        final content = connected
            ? 'Vehicle connected · Acquiring location'
            : aaActive
                ? 'Android Auto active · Monitoring active'
                : vehicleSessionActive
                    ? 'Monitoring active'
                    : 'Waiting for vehicle Bluetooth...';
        service.setForegroundNotificationInfo(
            title: 'SpeedShield', content: content);
      }
    });
    if (vehicleDetectionService.isConnectedToVehicle ||
        vehicleDetectionService.isAndroidAutoActive) {
      vehicleSessionActive = true;
      sessionStartTime ??= DateTime
          .now(); // don't overwrite if already restored from SharedPrefs
      btStartupGraceUntil ??= DateTime.now().add(kBtStartupGpsGraceWindow);
      btStartupFastAttemptsRemaining =
          max(btStartupFastAttemptsRemaining, kColdStartGpsFastAttempts);
      acquisitionState = AcquisitionState.acquiring;
      final coldPrefs = await SharedPreferences.getInstance();
      if ((coldPrefs.getInt('vehicle_session_start_ms') ?? 0) == 0) {
        await persistRuntimeSnapshot(
          monitoringActive: true,
          vehicleSessionActive: true,
          sessionStartTime: sessionStartTime,
          acquisitionState: acquisitionState,
          currentVehicleConnected: vehicleDetectionService.isConnectedToVehicle,
          currentVehicleName: coldPrefs.getString(kCurrentVehicleNameKey) ??
              coldPrefs.getString('bt_device_name') ??
              '',
          currentVehicleAddress:
              coldPrefs.getString(kCurrentVehicleAddressKey) ??
                  coldPrefs.getString('bt_device_address') ??
                  '',
          lastPosition: lastPosition,
        );
        _diag(
            'Persisted cold-start vehicle session timestamp at $sessionStartTime');
      }
    }
    monitoringEnabled = vehicleSessionActive;
  }

  bool autoDetectSessionActive = false;
  Future<bool> isAutoDetectInVehicle() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    return prefs.getBool(kRawActivityInVehicleKey) ?? false;
  }

  Future<void> persistSessionState(bool active, {DateTime? startedAt}) async {
    await persistRuntimeSnapshot(
      monitoringActive: active,
      vehicleSessionActive: active,
      sessionStartTime: active ? (startedAt ?? DateTime.now()) : null,
      acquisitionState:
          active ? AcquisitionState.acquiring : AcquisitionState.dormant,
      currentVehicleConnected: vehicleDetectionService.isConnectedToVehicle,
      currentVehicleName: '',
      currentVehicleAddress: '',
      lastPosition: lastPosition,
    );
  }

  bool hasFreshPosition() {
    if (lastPosition == null || lastPositionAt == null) return false;
    return DateTime.now().difference(lastPositionAt!) <=
        const Duration(seconds: 20);
  }

  double estimateFallbackDriftMeters(Position position, Duration age) {
    final rawSpeed = position.speed.isFinite ? position.speed : 0.0;
    final boundedSpeed = rawSpeed.clamp(0.0, kFallbackAbsoluteMaxSpeedMps);
    final effectiveSpeed = boundedSpeed < kFallbackWalkingPaceMps
        ? kFallbackWalkingPaceMps
        : boundedSpeed;
    return effectiveSpeed * age.inMilliseconds / 1000.0;
  }

  bool isFallbackPositionUsable(
      Position position, Duration age, String context) {
    final accuracy = position.accuracy;
    final recentEnough = age <= kBootstrapPositionMaxAge;
    final accurateEnough = accuracy <= kBootstrapPositionMaxAccuracyMeters;
    final estimatedDriftMeters = estimateFallbackDriftMeters(position, age);
    final driftAcceptable =
        estimatedDriftMeters <= kFallbackMaxEstimatedDriftMeters;
    if (recentEnough && accurateEnough && driftAcceptable) {
      _diag(
          'Using guarded fallback position for $context (age=${age.inSeconds}s accuracy=${accuracy.toStringAsFixed(1)}m estimatedDrift=${estimatedDriftMeters.toStringAsFixed(1)}m speed=${position.speed.toStringAsFixed(1)}mps)');
      return true;
    }
    _diag(
        'Rejected fallback position for $context (age=${age.inSeconds}s accuracy=${accuracy.toStringAsFixed(1)}m estimatedDrift=${estimatedDriftMeters.toStringAsFixed(1)}m speed=${position.speed.toStringAsFixed(1)}mps recentEnough=$recentEnough accurateEnough=$accurateEnough driftAcceptable=$driftAcceptable)');
    return false;
  }

  Future<void> clearNativeBootstrapReadiness(
      {String reason = 'consumed'}) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    await prefs.setBool(kNativeLocationBootstrapReadyKey, false);
    await prefs.setBool(kNativeLocationBootstrapActiveKey, false);
    await prefs.setInt(kNativeLocationBootstrapReadyTimeMsKey, 0);
    await prefs.setString(kNativeLocationBootstrapReadySourceKey, '');
    await prefs.setBool(kNativeLocationBootstrapServiceOwnedFixKey, false);
    _diag('Cleared native bootstrap readiness after $reason');
  }

  Future<({Position position, bool serviceOwned})?>
      readNativeBootstrapPosition() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    final lat = prefs.getDouble(kBootstrapLocationLatKey) ??
        prefs.getDouble(kBootstrapLocationLatKey.replaceAll('_', '.'));
    final lon = prefs.getDouble(kBootstrapLocationLonKey) ??
        prefs.getDouble(kBootstrapLocationLonKey.replaceAll('_', '.'));
    final accuracy = prefs.getDouble(kBootstrapLocationAccuracyKey) ??
        prefs.getDouble(kBootstrapLocationAccuracyKey.replaceAll('_', '.'));
    final speed = prefs.getDouble(kBootstrapLocationSpeedKey) ??
        prefs.getDouble(kBootstrapLocationSpeedKey.replaceAll('_', '.')) ??
        0.0;
    final bearing = prefs.getDouble(kBootstrapLocationBearingKey) ??
        prefs.getDouble(kBootstrapLocationBearingKey.replaceAll('_', '.')) ??
        0.0;
    final timestampMs = prefs.getInt(kBootstrapLocationTimeMsKey) ??
        prefs.getInt(kBootstrapLocationTimeMsKey.replaceAll('_', '.')) ??
        0;
    final source = prefs.getString(kBootstrapLocationSourceKey) ??
        prefs.getString(kBootstrapLocationSourceKey.replaceAll('_', '.')) ??
        'unknown';
    final serviceOwned =
        prefs.getBool(kNativeLocationBootstrapServiceOwnedFixKey) ?? false;
    if (lat == null || lon == null || accuracy == null || timestampMs <= 0) {
      return null;
    }
    final timestamp = DateTime.fromMillisecondsSinceEpoch(timestampMs);
    final age = DateTime.now().difference(timestamp);
    final alwaysTrustCandidate = shouldTrustNativeBootstrapCandidate(
      age: age,
      accuracyMeters: accuracy,
      serviceOwned: false,
    );
    if (age > kNativeBootstrapPositionMaxAge) {
      _diag(
          'Rejected native bootstrap location from $source because age=${age.inSeconds}s');
      return null;
    }
    if (accuracy > kNativeBootstrapPositionMaxAccuracyMeters) {
      _diag(
          'Rejected native bootstrap location from $source because accuracy=${accuracy.toStringAsFixed(1)}m');
      return null;
    }
    final speedLooksUsable = speed >= 2.0;
    final bearingLooksUsable = bearing > 0.0;
    if (!alwaysTrustCandidate &&
        !speedLooksUsable &&
        !bearingLooksUsable &&
        accuracy > 60.0) {
      _diag(
          'Rejected native bootstrap location from $source because it is low-confidence warmup data (accuracy=${accuracy.toStringAsFixed(1)}m speed=${speed.toStringAsFixed(1)} bearing=${bearing.toStringAsFixed(1)})');
      return null;
    }
    _diag(
        'Read native bootstrap location from $source age=${age.inSeconds}s accuracy=${accuracy.toStringAsFixed(1)}m speed=${speed.toStringAsFixed(1)} bearing=${bearing.toStringAsFixed(1)} serviceOwned=$serviceOwned');
    return (
      position: Position(
        longitude: lon,
        latitude: lat,
        timestamp: timestamp,
        accuracy: accuracy,
        altitude: 0,
        altitudeAccuracy: 0,
        heading: bearing,
        headingAccuracy: 0,
        speed: speed,
        speedAccuracy: 0,
      ),
      serviceOwned: serviceOwned,
    );
  }

  Future<Position?> getReadinessStreamPosition(Duration timeout) async {
    StreamSubscription<Position>? subscription;
    try {
      final completer = Completer<Position?>();
      const locationSettings = LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        distanceFilter: 0,
      );
      Timer? timeoutTimer;
      timeoutTimer = Timer(timeout, () {
        if (!completer.isCompleted) {
          completer.complete(null);
        }
      });
      subscription = Geolocator.getPositionStream(
        locationSettings: locationSettings,
      ).listen(
        (position) {
          final age = DateTime.now().difference(position.timestamp);
          final freshEnough = !age.isNegative && age <= kReadinessStreamMaxAge;
          final accurateEnough =
              position.accuracy <= kReadinessStreamMaxAccuracyMeters;
          if (!freshEnough || !accurateEnough) {
            _diag(
                'Ignoring readiness-stream position age=${age.inSeconds}s accuracy=${position.accuracy.toStringAsFixed(1)}m freshEnough=$freshEnough accurateEnough=$accurateEnough');
            return;
          }
          if (!completer.isCompleted) {
            completer.complete(position);
          }
        },
        onError: (Object error, StackTrace stackTrace) {
          _diag('Readiness position stream failed: $error');
          if (!completer.isCompleted) {
            completer.complete(null);
          }
        },
      );
      final position = await completer.future;
      timeoutTimer.cancel();
      if (position != null) {
        _diag(
            'Readiness position stream produced first usable fix accuracy=${position.accuracy.toStringAsFixed(1)}m speed=${position.speed.toStringAsFixed(1)}mps');
      }
      return position;
    } finally {
      await subscription?.cancel();
    }
  }

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
    // NativeLocationBootstrap writes kNativePositionUpdatedMsKey on every fix
    // (~2s interval). If the timestamp is <10s old, the bootstrap_location_*
    // keys hold a live position from the native foreground service — consume
    // it immediately and skip all Dart GPS paths. This bypasses OxygenOS
    // background GPS restriction because the native layer has unrestricted
    // hardware access via the foreground service.
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

    final nativeBootstrapActive =
        bootstrapPrefs.getBool(kNativeLocationBootstrapActiveKey) ?? false;
    final nativeBootstrapReady =
        bootstrapPrefs.getBool(kNativeLocationBootstrapReadyKey) ?? false;
    final nativeBootstrapTimedOut =
        bootstrapPrefs.getBool(kNativeLocationBootstrapTimedOutKey) ?? false;
    final nativeBootstrapServiceOwnedFix =
        bootstrapPrefs.getBool(kNativeLocationBootstrapServiceOwnedFixKey) ??
            false;
    final bootstrapStartedMs =
        bootstrapPrefs.getInt('native_location_bootstrap_started_ms') ??
            bootstrapPrefs.getInt(
                'native_location_bootstrap_started_ms'.replaceAll('_', '.')) ??
            0;
    final bootstrapAge = bootstrapStartedMs > 0
        ? DateTime.now()
            .difference(DateTime.fromMillisecondsSinceEpoch(bootstrapStartedMs))
        : Duration.zero;

    final nativeBootstrapSample = await readNativeBootstrapPosition();
    final nativeBootstrap = nativeBootstrapSample?.position;
    final nativeBootstrapIsServiceOwned =
        nativeBootstrapSample?.serviceOwned ?? nativeBootstrapServiceOwnedFix;
    if (nativeBootstrapReady &&
        nativeBootstrap != null &&
        nativeBootstrapIsServiceOwned) {
      consecutiveLocationTimeouts = 0;
      btStartupFastAttemptsRemaining = 0;
      lastPositionAt = DateTime.now();
      lastSuccessfulPositionAt = lastPositionAt;
      await clearNativeBootstrapReadiness(reason: 'ready_fix_consumed');
      _diag(
          'Native bootstrap declared readiness; background loop is consuming native-owned first fix');
      return nativeBootstrap;
    }

    final nativeBootstrapTimestamp = nativeBootstrap?.timestamp;
    final nativeBootstrapAge = nativeBootstrapTimestamp == null
        ? null
        : DateTime.now().difference(nativeBootstrapTimestamp);
    final nativeBootstrapAlwaysTrust = nativeBootstrap != null &&
        nativeBootstrapAge != null &&
        shouldTrustNativeBootstrapCandidate(
          age: nativeBootstrapAge,
          accuracyMeters: nativeBootstrap.accuracy,
          serviceOwned: false,
        );
    final nativeBootstrapServiceOwnedTrust = nativeBootstrap != null &&
        nativeBootstrapAge != null &&
        shouldTrustNativeBootstrapCandidate(
          age: nativeBootstrapAge,
          accuracyMeters: nativeBootstrap.accuracy,
          serviceOwned: nativeBootstrapIsServiceOwned,
        );
    final nativeBootstrapPassiveFallback = nativeBootstrap != null &&
        nativeBootstrapTimestamp != null &&
        nativeBootstrapAge != null &&
        nativeBootstrapAge <= kNativeBootstrapFallbackMaxAge &&
        nativeBootstrap.accuracy <= kNativeBootstrapFallbackAccuracyMeters;

    if (nativeBootstrapReady && nativeBootstrap != null) {
      if (nativeBootstrapAlwaysTrust || nativeBootstrapServiceOwnedTrust) {
        consecutiveLocationTimeouts = 0;
        btStartupFastAttemptsRemaining = 0;
        lastPositionAt = DateTime.now();
        lastSuccessfulPositionAt = lastPositionAt;
        await clearNativeBootstrapReadiness(
          reason: nativeBootstrapAlwaysTrust
              ? 'ready_fix_consumed_always_trust'
              : 'ready_fix_consumed_service_owned',
        );
        _diag(
            'Native bootstrap declared readiness; background loop is consuming first fix (serviceOwned=$nativeBootstrapIsServiceOwned age=${nativeBootstrapAge.inSeconds}s accuracy=${nativeBootstrap.accuracy.toStringAsFixed(1)}m)');
        return nativeBootstrap;
      }
      _diag(
          'Ignoring native bootstrap readiness because candidate failed trust policy (serviceOwned=$nativeBootstrapIsServiceOwned age=${nativeBootstrapAge?.inSeconds}s accuracy=${nativeBootstrap.accuracy.toStringAsFixed(1)}m)');
    }

    final shouldWaitForNativeBootstrap = nativeBootstrapActive &&
        !nativeBootstrapReady &&
        !nativeBootstrapTimedOut &&
        lastSuccessfulPositionAt == null &&
        bootstrapAge < kNativeBootstrapReadyWait &&
        !nativeBootstrapServiceOwnedTrust &&
        !nativeBootstrapAlwaysTrust;
    if (shouldWaitForNativeBootstrap) {
      _diag(
          'Native bootstrap not formally ready yet (age=${bootstrapAge.inSeconds}s); continuing active acquisition instead of idling blind');
    }

    if (nativeBootstrapServiceOwnedTrust) {
      consecutiveLocationTimeouts = 0;
      btStartupFastAttemptsRemaining = 0;
      lastPositionAt = DateTime.now();
      lastSuccessfulPositionAt = lastPositionAt;
      await clearNativeBootstrapReadiness(reason: 'fresh_candidate_consumed');
      _diag(
          'Using service-owned native bootstrap candidate before Dart fallback (accuracy=${nativeBootstrap.accuracy.toStringAsFixed(1)}m age=${nativeBootstrapAge.inSeconds}s)');
      return nativeBootstrap;
    }

    if (nativeBootstrapPassiveFallback && !nativeBootstrapIsServiceOwned) {
      if (lastSuccessfulPositionAt == null) {
        consecutiveLocationTimeouts = 0;
        btStartupFastAttemptsRemaining = 0;
        lastPositionAt = DateTime.now();
        lastSuccessfulPositionAt = lastPositionAt;
        await clearNativeBootstrapReadiness(
            reason: 'fresh_candidate_consumed_non_service_owned');
        _diag(
            'Accepting fresh non-service-owned native bootstrap candidate during first-fix acquisition instead of blocking on provenance');
        return nativeBootstrap;
      }
      _diag(
          'Observed fresh native bootstrap candidate but it was not service-owned; continuing live acquisition instead of trusting passive fix');
    }

    if (nativeBootstrap != null &&
        nativeBootstrapIsServiceOwned &&
        (useFastColdStartAttempt || lastSuccessfulPositionAt == null)) {
      consecutiveLocationTimeouts = 0;
      btStartupFastAttemptsRemaining = 0;
      lastPositionAt = DateTime.now();
      lastSuccessfulPositionAt = lastPositionAt;
      await clearNativeBootstrapReadiness(
          reason: 'fallback_candidate_consumed');
      _diag(
          'Using service-owned native bootstrap candidate after readiness wait/fallback path');
      return nativeBootstrap;
    }

    final shouldUseReadinessStream = lastSuccessfulPositionAt == null;
    if (shouldUseReadinessStream) {
      final readinessPosition =
          await getReadinessStreamPosition(kReadinessStreamAttemptTimeout);
      if (readinessPosition != null) {
        if (consecutiveLocationTimeouts > 0) {
          _diag(
              'Recovered GPS via readiness stream after $consecutiveLocationTimeouts consecutive timeout(s)');
        }
        consecutiveLocationTimeouts = 0;
        btStartupFastAttemptsRemaining = 0;
        lastPositionAt = DateTime.now();
        lastSuccessfulPositionAt = lastPositionAt;
        return readinessPosition;
      }
    }

    try {
      final position = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.bestForNavigation,
        timeLimit: attemptTimeout,
      );
      if (consecutiveLocationTimeouts > 0) {
        _diag(
            'Recovered GPS after $consecutiveLocationTimeouts consecutive timeout(s)');
      }
      consecutiveLocationTimeouts = 0;
      btStartupFastAttemptsRemaining = 0;
      lastPositionAt = DateTime.now();
      lastSuccessfulPositionAt = lastPositionAt;
      return position;
    } on TimeoutException {
      consecutiveLocationTimeouts += 1;
      if (useFastColdStartAttempt && btStartupFastAttemptsRemaining > 0) {
        btStartupFastAttemptsRemaining -= 1;
      }
      _diag(
          'GPS acquisition timed out (count=$consecutiveLocationTimeouts timeout=${attemptTimeout.inSeconds}s coldStartFast=$useFastColdStartAttempt remainingFastAttempts=$btStartupFastAttemptsRemaining)');
      if (hasFreshPosition()) {
        _diag('Reusing fresh cached position after timeout');
        return lastPosition;
      }
      final fallback = await Geolocator.getLastKnownPosition();
      if (fallback != null) {
        final now = DateTime.now();
        final age = now.difference(fallback.timestamp);
        if (isFallbackPositionUsable(fallback, age, 'timeout')) {
          lastPositionAt = now;
          lastSuccessfulPositionAt = lastPositionAt;
          return fallback;
        }
      }
      return null;
    } catch (e) {
      _diag('GPS acquisition failed: $e');
      if (hasFreshPosition()) {
        _diag('Reusing fresh cached position after GPS failure');
        return lastPosition;
      }
      final fallback = await Geolocator.getLastKnownPosition();
      if (fallback != null) {
        final now = DateTime.now();
        final age = now.difference(fallback.timestamp);
        if (isFallbackPositionUsable(fallback, age, 'gps_failure')) {
          lastPositionAt = now;
          lastSuccessfulPositionAt = lastPositionAt;
          return fallback;
        }
      }
      return null;
    }
  }

  double haversineM(double lat1, double lon1, double lat2, double lon2) {
    const r = 6371000.0;
    final dLat = (lat2 - lat1) * pi / 180;
    final dLon = (lon2 - lon1) * pi / 180;
    final a = sin(dLat / 2) * sin(dLat / 2) +
        cos(lat1 * pi / 180) *
            cos(lat2 * pi / 180) *
            sin(dLon / 2) *
            sin(dLon / 2);
    return r * 2 * atan2(sqrt(a), sqrt(1 - a));
  }

  Duration nextInterval(Position? pos, List<SpeedCamera> cams) {
    if (pos == null || cams.isEmpty) return const Duration(seconds: 15);
    double minDist = double.infinity;
    for (final cam in cams) {
      final d =
          haversineM(pos.latitude, pos.longitude, cam.latitude, cam.longitude);
      if (d < minDist) minDist = d;
    }
    if (minDist < 250) return const Duration(seconds: 1); // imminent — 1s
    if (minDist < 1000) return const Duration(seconds: 2); // alert-ready — 2s
    if (minDist < 2000) return const Duration(seconds: 8); // vigilant — 8s
    return const Duration(seconds: 15); // coasting — 15s
  }

  bool loopRunning = false;

  Future<void> runLoop() async {
    {
      final lockPrefs = await SharedPreferences.getInstance();
      await lockPrefs.reload();
      final existingLockMs = lockPrefs.getInt('loop_lock_ms') ?? 0;
      final lockAge = DateTime.now().millisecondsSinceEpoch - existingLockMs;
      if (existingLockMs > 0 && lockAge < 30000) {
        _diag(
            'loop_lock_ms is fresh ($lockAge ms old) — another instance is running, exiting');
        return;
      }
      await lockPrefs.setInt(
          'loop_lock_ms', DateTime.now().millisecondsSinceEpoch);
    }
    if (loopRunning) {
      _diag('runLoop called while already running — ignoring duplicate');
      return;
    }
    loopRunning = true;
    try {
      while (true) {
        try {
          // ——— Session safety valve ———
          if (vehicleSessionActive && sessionStartTime != null) {
            final sessionHours =
                DateTime.now().difference(sessionStartTime!).inHours;
            if (sessionHours >= maxSessionHours) {
              vehicleSessionActive = false;
              sessionStartTime = null;
              monitoringEnabled = false;
              lastPosition = null;
              lastPositionAt = null;
              final prefs = await SharedPreferences.getInstance();
              await prefs.setBool('aa_connected', false);
              await prefs.setBool(kRawAaConnectedKey, false);
              await prefs.setBool(kCurrentVehicleConnectedKey, false);
              await prefs.setString(kCurrentVehicleNameKey, '');
              await prefs.setString(kCurrentVehicleAddressKey, '');
              await persistRuntimeSnapshot(
                monitoringActive: false,
                vehicleSessionActive: false,
                sessionStartTime: null,
                acquisitionState: AcquisitionState.dormant,
                currentVehicleConnected: false,
                currentVehicleName: '',
                currentVehicleAddress: '',
                lastPosition: null,
              );
              autoDetectSessionActive = false;
              if (service is AndroidServiceInstance) {
                service.setForegroundNotificationInfo(
                  title: 'SpeedShield',
                  content: 'Waiting for vehicle Bluetooth...',
                );
              }
              // Safety valve fired — service is no longer needed. Shut it down.
              // Native receivers (BluetoothReceiver, AndroidAutoMonitor) will
              // restart it when a new drive begins.
              if (service is AndroidServiceInstance) {
                _diag('Stopping service: 4-hour safety valve fired');
                await service.stopSelf();
              }
              break;
            }
          }

          // ——— No active session — service should not be running ———
          // If reached (e.g. service was restarted mid-park by watchdog),
          // shut down immediately rather than idle-polling.
          if (settings.activationMode == ActivationMode.vehicleBluetooth &&
              !vehicleSessionActive) {
            if (service is AndroidServiceInstance) {
              _diag(
                  'Stopping service: vehicleBluetooth mode with no active session');
              await service.stopSelf();
            }
            break;
          }

          if (settings.activationMode == ActivationMode.autoDetect) {
            final autoDetectInVehicle = await isAutoDetectInVehicle();
            if (!autoDetectInVehicle &&
                !autoDetectSessionActive &&
                !vehicleSessionActive) {
              await persistSessionState(false);
              if (service is AndroidServiceInstance) {
                _diag(
                    'Stopping service: auto-detect mode not in vehicle and no active session');
                await service.stopSelf();
              }
              break;
            }
          }

          final position = await getBestEffortPosition();
          if (position == null) {
            acquisitionState = (vehicleSessionActive ||
                    autoDetectSessionActive ||
                    monitoringEnabled)
                ? AcquisitionState.acquiring
                : AcquisitionState.dormant;
            final heartbeatPrefs = await SharedPreferences.getInstance();
            await persistRuntimeSnapshot(
              monitoringActive: monitoringEnabled,
              vehicleSessionActive: vehicleSessionActive,
              sessionStartTime: sessionStartTime,
              acquisitionState: acquisitionState,
              currentVehicleConnected:
                  vehicleDetectionService.isConnectedToVehicle,
              currentVehicleName:
                  heartbeatPrefs.getString(kCurrentVehicleNameKey) ??
                      heartbeatPrefs.getString('bt_device_name') ??
                      '',
              currentVehicleAddress:
                  heartbeatPrefs.getString(kCurrentVehicleAddressKey) ??
                      heartbeatPrefs.getString('bt_device_address') ??
                      '',
              lastPosition: lastPosition,
            );
            await heartbeatPrefs.setInt(
                'loop_lock_ms', DateTime.now().millisecondsSinceEpoch);
            final staleFor = lastSuccessfulPositionAt == null
                ? null
                : DateTime.now().difference(lastSuccessfulPositionAt!);
            _diag(
                'No position available this cycle; heartbeat kept alive while waiting for GPS${staleFor == null ? '' : ' (last success ${staleFor.inSeconds}s ago)'}');
            final withinBtStartupGrace = btStartupGraceUntil != null &&
                DateTime.now().isBefore(btStartupGraceUntil!);
            if (settings.activationMode == ActivationMode.vehicleBluetooth &&
                vehicleSessionActive &&
                staleFor != null &&
                staleFor >= kColdStartNoFixShutdownGrace &&
                !withinBtStartupGrace &&
                !vehicleDetectionService.isConnectedToVehicle &&
                !vehicleDetectionService.isAndroidAutoActive) {
              _diag(
                  'Stopping service: GPS stale for ${kColdStartNoFixShutdownGrace.inMinutes}m and both BT/AA are gone');
              vehicleSessionActive = false;
              monitoringEnabled = false;
              lastPosition = null;
              lastPositionAt = null;
              await clearAlertDispatchState();
              await persistSessionState(false);
              if (service is AndroidServiceInstance) {
                await service.stopSelf();
              }
              break;
            }
            final bootstrapPrefs = await SharedPreferences.getInstance();
            await bootstrapPrefs.reload();
            final nativeBootstrapActive =
                bootstrapPrefs.getBool(kNativeLocationBootstrapActiveKey) ??
                    false;
            final nativeBootstrapReady =
                bootstrapPrefs.getBool(kNativeLocationBootstrapReadyKey) ??
                    false;
            final interval = nativeBootstrapActive && !nativeBootstrapReady
                ? const Duration(milliseconds: 750)
                : btStartupFastAttemptsRemaining > 0
                    ? const Duration(seconds: 1)
                    : consecutiveLocationTimeouts >= 3
                        ? const Duration(seconds: 2)
                        : nextInterval(lastPosition, cameras);
            await Future.delayed(interval);
            continue;
          }
          lastPosition = position;
          lastPositionAt = DateTime.now();
          lastSuccessfulPositionAt = lastPositionAt;
          acquisitionState = AcquisitionState.monitoring;

          if (settings.activationMode == ActivationMode.vehicleBluetooth &&
              vehicleSessionActive) {
            final btConnected = vehicleDetectionService.isConnectedToVehicle;
            final aaConnected = vehicleDetectionService.isAndroidAutoActive;

            if (btConnected || aaConnected) {
              monitoringEnabled = true;
              acquisitionState = AcquisitionState.monitoring;
              btStartupGraceUntil = null;
              if (service is AndroidServiceInstance) {
                service.setForegroundNotificationInfo(
                  title: 'SpeedShield',
                  content: aaConnected
                      ? 'Android Auto active · Monitoring active'
                      : 'Vehicle connected · Monitoring active',
                );
              }
            } else {
              final now = DateTime.now();
              final stillInStartupGrace = btStartupGraceUntil != null &&
                  now.isBefore(btStartupGraceUntil!);
              final stillWaitingForFirstFix = lastSuccessfulPositionAt == null;
              if (stillInStartupGrace || stillWaitingForFirstFix) {
                monitoringEnabled = true;
                acquisitionState = AcquisitionState.acquiring;
                _diag(
                    'BT/AA absent during startup grace or before first usable fix; keeping session alive for GPS recovery');
              } else {
                _diag(
                    'BT and AA both false during active session; waiting 45s grace before shutdown');
                monitoringEnabled = false;
                acquisitionState = AcquisitionState.teardownGrace;
                final sessionPrefs = await SharedPreferences.getInstance();
                await persistRuntimeSnapshot(
                  monitoringActive: false,
                  vehicleSessionActive: vehicleSessionActive,
                  sessionStartTime: sessionStartTime,
                  acquisitionState: acquisitionState,
                  currentVehicleConnected: false,
                  currentVehicleName:
                      sessionPrefs.getString(kCurrentVehicleNameKey) ??
                          sessionPrefs.getString('bt_device_name') ??
                          '',
                  currentVehicleAddress:
                      sessionPrefs.getString(kCurrentVehicleAddressKey) ??
                          sessionPrefs.getString('bt_device_address') ??
                          '',
                  lastPosition: lastPosition,
                );
                await sessionPrefs.setInt(
                    'loop_lock_ms', DateTime.now().millisecondsSinceEpoch);
                await Future.delayed(kBtSignalDropGraceWindow);

                final stillBtConnected =
                    vehicleDetectionService.isConnectedToVehicle;
                final stillAaConnected =
                    vehicleDetectionService.isAndroidAutoActive;
                if (stillBtConnected || stillAaConnected) {
                  _diag(
                      'Signal recovered during grace cycle; resuming active monitoring');
                  monitoringEnabled = true;
                } else {
                  vehicleSessionActive = false;
                  monitoringEnabled = false;
                  acquisitionState = AcquisitionState.dormant;
                  btStartupGraceUntil = null;
                  lastPosition = null;
                  lastPositionAt = null;
                  await clearAlertDispatchState();
                  await persistRuntimeSnapshot(
                    monitoringActive: false,
                    vehicleSessionActive: false,
                    sessionStartTime: null,
                    acquisitionState: AcquisitionState.dormant,
                    currentVehicleConnected: false,
                    currentVehicleName: '',
                    currentVehicleAddress: '',
                    lastPosition: null,
                  );
                  autoDetectSessionActive = false;
                  if (service is AndroidServiceInstance) {
                    service.setForegroundNotificationInfo(
                      title: 'SpeedShield',
                      content: 'Waiting for vehicle Bluetooth...',
                    );
                  }
                  if (service is AndroidServiceInstance) {
                    _diag(
                        'Stopping service: BT/AA still absent after 45s grace');
                    await service.stopSelf();
                  }
                  break;
                }
              }
            }
          }

          if (settings.activationMode == ActivationMode.autoDetect) {
            final autoDetectInVehicle = await isAutoDetectInVehicle();
            final speedMph = position.speed * 2.23694;
            if (autoDetectSessionActive || vehicleSessionActive) {
              monitoringEnabled = autoDetectInVehicle;
              acquisitionState = autoDetectInVehicle
                  ? AcquisitionState.monitoring
                  : AcquisitionState.dormant;
              if (!autoDetectInVehicle) {
                autoDetectSessionActive = false;
                vehicleSessionActive = false;
                sessionStartTime = null;
                await persistRuntimeSnapshot(
                  monitoringActive: false,
                  vehicleSessionActive: false,
                  sessionStartTime: null,
                  acquisitionState: AcquisitionState.dormant,
                  currentVehicleConnected: false,
                  currentVehicleName: '',
                  currentVehicleAddress: '',
                  lastPosition: lastPosition,
                );
                if (service is AndroidServiceInstance) {
                  _diag(
                      'Stopping service: auto-detect session no longer in vehicle');
                  await service.stopSelf();
                }
                break;
              }
            } else if (speedMph >= AlertSettings.autoDetectSpeedThresholdMph) {
              autoDetectSpeedSince ??= DateTime.now();
              if (DateTime.now().difference(autoDetectSpeedSince!).inSeconds >=
                  60) {
                monitoringEnabled = true;
                autoDetectSessionActive = true;
                vehicleSessionActive = true;
                acquisitionState = AcquisitionState.acquiring;
                sessionStartTime ??= DateTime.now();
                proximityService.resetAlertState();
                await clearAlertDispatchState();
                await persistRuntimeSnapshot(
                  monitoringActive: true,
                  vehicleSessionActive: true,
                  sessionStartTime: sessionStartTime,
                  acquisitionState: acquisitionState,
                  currentVehicleConnected:
                      vehicleDetectionService.isConnectedToVehicle,
                  currentVehicleName: '',
                  currentVehicleAddress: '',
                  lastPosition: lastPosition,
                );
                _diag(
                    'Auto-detect session activated at $sessionStartTime after sustained speed ${speedMph.toStringAsFixed(1)} mph');
              }
            } else {
              autoDetectSpeedSince = null;
              monitoringEnabled = false;
            }
          }

          if (!monitoringEnabled) {
            acquisitionState = vehicleSessionActive
                ? AcquisitionState.acquiring
                : AcquisitionState.dormant;
            final idlePrefs = await SharedPreferences.getInstance();
            await persistRuntimeSnapshot(
              monitoringActive: false,
              vehicleSessionActive: vehicleSessionActive,
              sessionStartTime: sessionStartTime,
              acquisitionState: acquisitionState,
              currentVehicleConnected:
                  vehicleDetectionService.isConnectedToVehicle,
              currentVehicleName: idlePrefs.getString(kCurrentVehicleNameKey) ??
                  idlePrefs.getString('bt_device_name') ??
                  '',
              currentVehicleAddress:
                  idlePrefs.getString(kCurrentVehicleAddressKey) ??
                      idlePrefs.getString('bt_device_address') ??
                      '',
              lastPosition: lastPosition,
            );
            await idlePrefs.setInt(
                'loop_lock_ms', DateTime.now().millisecondsSinceEpoch);
            if (service is AndroidServiceInstance) {
              final statusText = switch (settings.activationMode) {
                ActivationMode.vehicleBluetooth =>
                  'Waiting for vehicle Bluetooth...',
                ActivationMode.autoDetect => 'Waiting for driving speed...',
                ActivationMode.alwaysOn => 'Monitoring active',
              };
              service.setForegroundNotificationInfo(
                  title: 'SpeedShield', content: statusText);
            }
            final interval = nextInterval(lastPosition, cameras);
            await Future.delayed(interval);
            continue;
          }

          final results = proximityService.check(
            userLat: position.latitude,
            userLon: position.longitude,
            userHeading: position.heading,
            userSpeed: position.speed < 0 ? 0.0 : position.speed,
            cameras: cameras,
            settings: settings,
          );

          if (service is AndroidServiceInstance) {
            final allResults = [...results.primary, ...results.secondary];
            if (allResults.isNotEmpty) {
              final closest = allResults.reduce(
                  (a, b) => a.distanceMeters < b.distanceMeters ? a : b);
              service.setForegroundNotificationInfo(
                title: 'SpeedShield — Camera nearby',
                content:
                    '${closest.camera.name} · ${closest.distanceMeters.round()}m',
              );
            } else {
              service.setForegroundNotificationInfo(
                title: 'SpeedShield',
                content: 'Monitoring active · ${cameras.length} cameras loaded',
              );
            }
          }

          final prefs = await SharedPreferences.getInstance();
          await prefs.reload();
          await persistRuntimeSnapshot(
            monitoringActive: monitoringEnabled,
            vehicleSessionActive: vehicleSessionActive,
            sessionStartTime: sessionStartTime,
            acquisitionState: acquisitionState,
            currentVehicleConnected:
                vehicleDetectionService.isConnectedToVehicle,
            currentVehicleName: prefs.getString(kCurrentVehicleNameKey) ??
                prefs.getString('bt_device_name') ??
                '',
            currentVehicleAddress: prefs.getString(kCurrentVehicleAddressKey) ??
                prefs.getString('bt_device_address') ??
                '',
            lastPosition: position,
          );
          await prefs.setInt(
              'loop_lock_ms', DateTime.now().millisecondsSinceEpoch);

          final appInForeground = prefs.getBool(kAppForegroundKey) ?? false;
          final foregroundRecentlyActive = await isForegroundRecentlyReleased();
          final vehicleSessionOwnsAlerts =
              settings.activationMode == ActivationMode.vehicleBluetooth &&
                  vehicleSessionActive;
          final allowBackgroundDispatch = vehicleSessionOwnsAlerts ||
              (!appInForeground && !foregroundRecentlyActive);
          if (allowBackgroundDispatch) {
            if (results.primary.isNotEmpty || results.secondary.isNotEmpty) {
              _diag(
                  'Dispatching alerts from background: primary=${results.primary.length} secondary=${results.secondary.length} vehicleSessionOwnsAlerts=$vehicleSessionOwnsAlerts appInForeground=$appInForeground foregroundRecentlyActive=$foregroundRecentlyActive');
            }
            for (final alert in results.secondary) {
              if (await shouldDispatchAlert(
                'secondary',
                alert,
                false,
                currentSpeedMps: position.speed < 0 ? 0.0 : position.speed,
              )) {
                await alertService.alertSecondary(alert.camera, settings,
                    actualDistanceMeters: alert.distanceMeters,
                    source: 'background_secondary');
              }
            }
            for (final alert in results.primary) {
              if (await shouldDispatchAlert(
                'primary',
                alert,
                false,
                currentSpeedMps: position.speed < 0 ? 0.0 : position.speed,
              )) {
                await alertService.alertPrimary(alert.camera, settings,
                    actualDistanceMeters: alert.distanceMeters,
                    source: 'background_primary');
              }
            }
          } else if (results.primary.isNotEmpty ||
              results.secondary.isNotEmpty) {
            _diag(
                'Skipping background TTS because foreground retains authority primary=${results.primary.length} secondary=${results.secondary.length} appInForeground=$appInForeground foregroundRecentlyActive=$foregroundRecentlyActive vehicleSessionOwnsAlerts=$vehicleSessionOwnsAlerts');
          }

          settings = await settingsService.load();
          cameras = await cameraService.getAllCameras();
          if (settings.activationMode == ActivationMode.vehicleBluetooth) {
            final prefs2 = await SharedPreferences.getInstance();
            await prefs2.reload();
            vehicleDetectionService.approvedAddresses =
                _loadApprovedAddresses(prefs2);
          }
        } catch (e, stack) {
          _diag('background loop error: $e\n$stack');
        }

        final interval = nextInterval(lastPosition, cameras);
        await Future.delayed(interval);
      }
    } finally {
      loopRunning = false;
      final lockPrefs = await SharedPreferences.getInstance();
      await lockPrefs.setInt('loop_lock_ms', 0);
    }
  }

  runLoop();
}
