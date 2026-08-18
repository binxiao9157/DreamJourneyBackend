from typing import Any, Dict, Optional

from app.core.config import Settings
from app.async_effects.contracts import resolve_async_effect_runtime_status
from app.services.deepseek import ArchiveImageAnalysisProviderFactory
from app.services.digital_human_access import DigitalHumanAccessPolicy
from app.services.identity_bindings import identity_challenge_runtime_descriptor
from app.services.route_authentication import resolve_route_authentication_mode
from app.services.route_ownership import RouteOwnershipRegistry
from app.services.release_policy import ReleasePolicyService, parse_release_policy_feature_set
from app.services.recovery_access import RecoveryAccessPolicy
from app.services.safety_policy import SafetyPolicy
from app.services.provider_runtime import ProviderRuntimeInventory, ProviderRuntimeStatus
from app.services.tokens import TokenService
from app.services.tts import VoiceCloneTTSProviderFactory
from app.services.voice_clone import VoiceCloneProviderFactory, configured_voice_clone_speaker_ids
from app.services.voice_identity_eligibility import (
    voice_identity_eligibility_runtime_descriptor,
)
from app.services.voice_clone_operation_capabilities import (
    build_voice_clone_operation_capability_matrix,
)
from app.services.runtime_capabilities import (
    RuntimeCapabilityComposer,
    RuntimeCapabilityInput,
)
from app.services.runtime_capability_control import (
    RuntimeCapabilityControlDecision,
    RuntimeCapabilityControlRegistry,
)
from app.services.apns_delivery import APNSConfiguration, APNSDeliveryError, apns_runtime_descriptor


class RuntimeConfigService:
    def __init__(
        self,
        settings: Settings,
        *,
        provider_inventory: Optional[ProviderRuntimeInventory] = None,
        capability_control_registry: Optional[RuntimeCapabilityControlRegistry] = None,
        async_effect_schema_ready: bool = False,
    ):
        self.settings = settings
        # Tests and maintenance commands may instantiate this service outside
        # FastAPI startup.  They still receive the same fail-closed inventory,
        # marked as a runtime validation rather than a startup receipt.
        self.provider_inventory = provider_inventory or ProviderRuntimeInventory(settings)
        self.capability_control_registry = capability_control_registry
        self.async_effect_schema_ready = bool(async_effect_schema_ready)

    def public_config(self) -> Dict[str, Any]:
        archive_image_analysis = ArchiveImageAnalysisProviderFactory(self.settings).make()
        voice_clone_provider = VoiceCloneProviderFactory(self.settings).make()
        voice_clone_tts_provider = VoiceCloneTTSProviderFactory(self.settings).make()
        voice_clone_speaker_ids = self._voice_clone_speaker_ids()
        voice_identity_eligibility = voice_identity_eligibility_runtime_descriptor(self.settings)
        voice_training_admission_enabled = (
            voice_clone_provider.is_configured
            and voice_identity_eligibility["ready"]
        )
        voice_training_admission_reason = (
            "ready"
            if voice_training_admission_enabled
            else (
                voice_identity_eligibility["reason"]
                if not voice_identity_eligibility["ready"]
                else "voiceCloneProviderUnavailable"
            )
        )
        voice_clone_operation_matrix = build_voice_clone_operation_capability_matrix(
            training_provider=voice_clone_provider,
            synthesis_provider=voice_clone_tts_provider,
            training_admission_enabled=voice_training_admission_enabled,
            training_admission_reason=voice_training_admission_reason,
            deletion_worker_enabled=self.settings.voice_clone_deletion_worker_enabled,
        )
        digital_human_asset_mode = self._digital_human_asset_mode()
        digital_human_access = DigitalHumanAccessPolicy().blocked_mobile_contract()
        route_ownership_audit = RouteOwnershipRegistry().audit_summary()
        route_authentication_mode = resolve_route_authentication_mode(
            self.settings.environment,
            self.settings.auth_route_mode,
        )
        realtime_voice = TokenService(self.settings).realtime_config(user_id="runtime-capability")
        identity_challenge = identity_challenge_runtime_descriptor(self.settings)
        media_storage = self.provider_inventory.status_for("ownerTruthMediaStorage")
        media_processing = self.provider_inventory.status_for("ownerTruthMediaProcessing")
        identity_provider = self.provider_inventory.status_for("identityChallenge")
        release_policy = ReleasePolicyService(
            policy_revision=self.settings.release_policy_revision,
            min_client_build=self.settings.release_policy_min_client_build,
            ttl_seconds=self.settings.release_policy_ttl_seconds,
            emergency_revision=self.settings.release_policy_emergency_revision,
            emergency_disabled_features=parse_release_policy_feature_set(
                self.settings.release_policy_emergency_disabled_features
            ),
            enforced_features=parse_release_policy_feature_set(
                self.settings.release_policy_enforced_features
            ),
            closed_pilot_enabled_features=parse_release_policy_feature_set(
                self.settings.release_policy_closed_pilot_features
            ),
            authenticated_owner_v4_enabled=(
                self.settings.release_policy_authenticated_owner_v4_enabled
            ),
            capability_resolver=lambda capability: (
                self._provider_operational_ready(
                    self.provider_inventory.status_for(capability)
                )
            ),
            shadow_mode=self.settings.release_policy_command_mode != "enforce",
        )
        recovery_access = RecoveryAccessPolicy(
            mode=self.settings.recovery_access_mode,
            authority_epoch=self.settings.authority_epoch,
        )
        safety_policy = SafetyPolicy().evaluate("")
        async_effect_runtime = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self.settings.async_effect_v1_enabled,
            worker_enabled=self.settings.async_effect_worker_enabled,
            # Only a live store readiness probe may open the server-completion
            # lane. Configuration flags alone remain fail-closed.
            schema_ready=self.async_effect_schema_ready,
        )
        try:
            apns_delivery = apns_runtime_descriptor(
                APNSConfiguration(
                    provider=self.settings.apns_delivery_provider,
                    token_vault_provider=self.settings.apns_token_vault_provider,
                    topic=self.settings.apns_topic,
                    environment=self.settings.apns_environment,
                    max_attempts=self.settings.apns_max_attempts,
                    token_encryption_key_configured=bool(
                        self.settings.apns_token_encryption_key
                    ),
                    team_id=self.settings.apns_team_id,
                    key_id=self.settings.apns_key_id,
                    private_key_path=self.settings.apns_private_key_path,
                    request_timeout_seconds=self.settings.apns_request_timeout_seconds,
                    external_verified=self.settings.apns_external_verified,
                )
            )
        except APNSDeliveryError as exc:
            apns_delivery = {
                "schemaVersion": 1,
                "implemented": True,
                "enabled": False,
                "provider": "invalid",
                "tokenVault": "invalid",
                "environment": str(self.settings.apns_environment or ""),
                "topicConfigured": bool(self.settings.apns_topic),
                "externalVerified": False,
                "realProviderReady": False,
                "defaultReleaseVisible": False,
                "registrationEndpoint": "/devices/push-token",
                "deliveryReceiptStates": ["accepted", "arrived", "failed", "unknown"],
                "reason": exc.code,
            }
        capability_snapshots = self._capability_snapshots(
            archive_image_analysis=archive_image_analysis,
            identity_challenge=identity_challenge,
            provider_inventory=self.provider_inventory,
            release_policy=release_policy,
        )
        return {
            "environment": self.settings.environment,
            "baseURL": self.settings.public_base_url,
            "capabilitySnapshotSchemaVersion": RuntimeCapabilityComposer.SCHEMA_VERSION,
            "capabilitySnapshots": capability_snapshots,
            "providerInventory": self.provider_inventory.public_descriptor(),
            "runtimeCapabilityControl": self._capability_control_descriptor(),
            "capabilities": {
                "deepseekProxy": bool(self.settings.deepseek_api_key),
                "archiveImageAnalysis": archive_image_analysis.enabled,
                "ttsProxy": bool(self.settings.volcengine_api_key and self.settings.volcengine_voice_type),
                "realtimeToken": bool(realtime_voice.get("providerReady")),
                "amapDistrictProxy": bool(self.settings.amap_web_service_key),
                "kbSync": True,
                "familyCircle": True,
                "timeLetters": False,
                "echoDelayedReplies": False,
                # The legacy archive contract below remains metadata-only.
                # Actual M0 private-media capture has its own authenticated
                # Owner Truth route and is gated by this startup inventory.
                "archiveMediaUploadIntent": True,
                "ownerTruthMediaCapture": self._provider_operational_ready(media_storage),
                "ownerTruthMediaProcessing": self._provider_operational_ready(media_processing),
                "voiceClone": voice_clone_provider.is_configured,
                "digitalHumanSession": False,
                "digitalHumanSessionLease": False,
                "authSession": True,
                "identityChallenge": identity_provider.enabled,
                "releasePolicy": True,
                "asyncEffect": async_effect_runtime.enabled,
                "apnsDelivery": bool(apns_delivery["enabled"]),
            },
            "auth": {
                "mode": "opaqueAccessRefresh",
                "loginEndpoint": "/v2/auth/challenges",
                "legacyLoginEndpoint": "/auth/login",
                "legacyLoginEnabled": identity_challenge[
                    "legacyPhoneLoginEnabled"
                ],
                "refreshEndpoint": "/auth/refresh",
                "logoutEndpoint": "/auth/logout",
                "identityChallenge": identity_challenge,
                "tokenType": "Bearer",
                "accessTTLSeconds": max(60, self.settings.auth_access_ttl_seconds),
                "refreshTTLSeconds": max(
                    max(60, self.settings.auth_access_ttl_seconds) + 60,
                    self.settings.auth_refresh_ttl_seconds,
                ),
                "refreshRotation": True,
                "sessionContractVersion": 2,
                "tokenFamilyContractVersion": 1,
                "sessionLineageFields": ["tokenFamilyId", "sessionVersion"],
                "refreshReuseRevokesFamily": True,
                "legacyRefreshPolicy": "reauthRequired",
                "logoutScopes": ["session", "family", "allDevices"],
                "routeAuthentication": {
                    "mode": route_authentication_mode,
                    "routeCount": route_ownership_audit["routeCount"],
                    "authModeCounts": route_ownership_audit["authModeCounts"],
                    "unclassifiedCount": route_ownership_audit["unclassifiedCount"],
                    "productionEnforceReady": route_authentication_mode == "enforce",
                    "userAudience": "dreamjourney-user",
                    "machineAudience": "dreamjourney-backend",
                    "diagnosticHeaders": [
                        "X-DreamJourney-Route-Auth-Mode",
                        "X-DreamJourney-Route-Auth-Policy",
                        "X-DreamJourney-Route-Auth-Decision",
                        "X-DreamJourney-Route-Auth-Reason",
                    ],
                    "contractVersion": 1,
                },
                "ownershipMode": (
                    self.settings.auth_ownership_mode
                    if self.settings.auth_ownership_mode in {"shadow", "enforce"}
                    else "shadow"
                ),
                "crossAccountPolicy": {
                    "mode": (
                        self.settings.auth_ownership_mode
                        if self.settings.auth_ownership_mode in {"shadow", "enforce"}
                        else "shadow"
                    ),
                    "coveredPolicies": [
                        "careSnapshotRead",
                        "careSnapshotWrite",
                        "timeLetterDetail",
                        "familyInvitationAccept",
                        "familyMemberAccept",
                        "systemOnly",
                    ],
                    "diagnosticHeaders": [
                        "X-DreamJourney-Authorization-Policy",
                        "X-DreamJourney-Authorization-Decision",
                        "X-DreamJourney-Authorization-Reason",
                    ],
                    "productionEnforceReady": False,
                    "principalBoundRouteEnforcement": True,
                    "routeOwnershipAudit": {
                        "routeCount": route_ownership_audit["routeCount"],
                        "categoryCounts": route_ownership_audit["categoryCounts"],
                        "unclassifiedCount": route_ownership_audit["unclassifiedCount"],
                    },
                    "enforceBlockers": [
                        "smsIdentityProof",
                        "deployedShadowEvidence",
                    ],
                    "contractVersion": 1,
                },
                "legacyBackendTokenCompatible": False,
                "contractVersion": 2,
            },
            "releasePolicy": release_policy.public_descriptor(),
            "asyncEffect": {
                "enabled": async_effect_runtime.enabled,
                "workerEnabled": async_effect_runtime.worker_enabled,
                "serverCompletionAvailable": async_effect_runtime.allowed,
                "reason": async_effect_runtime.reason,
                "defaultReleaseVisible": False,
                "contractVersion": 1,
            },
            "notifications": {
                "apns": apns_delivery,
                "inAppMailbox": True,
                "contractVersion": 1,
            },
            "recovery": recovery_access.public_descriptor(),
            "safety": {
                "policyVersion": SafetyPolicy.POLICY_VERSION,
                "aiDisclosure": safety_policy.disclosure.model_dump(mode="json"),
                "neutralSafetyMode": "textOnly",
                "personaOnCrisis": "deny",
                "delayedReplyOnCrisis": "deny",
                "providerEffectsOnCrisis": "deny",
                "contractVersion": 1,
            },
            "archive": {
                "uploadIntentEndpoint": "/archive/media/upload-intent",
                "storageProvider": "mockObjectStorage",
                "providerDisplayName": "Mock Object Storage",
                "providerMode": "mock",
                "requiresClientUpload": False,
                "uploadURLScheme": "mock",
                "realProviderReady": False,
                "providerSwitchContractVersion": 1,
                "clientUploadAction": "metadataOnly",
                "supportedMediaKinds": ["audio", "video"],
                "audioFileSizeLimitMB": 50,
                "videoFileSizeLimitMB": 200,
                "uploadIntentTTLSeconds": 900,
            },
            "ownerTruthMedia": {
                "captureCapability": "ownerTruthMediaStorage",
                "processingCapability": "ownerTruthMediaProcessing",
                "uploadIntentEndpointTemplate": "/v2/vaults/{vaultId}/source-objects/upload-intents",
                "contentEndpointTemplate": "/v2/vaults/{vaultId}/source-objects/upload-intents/{intentId}/content",
                "supportedMediaKinds": ["document", "image", "audio", "video"],
                "contractVersion": 1,
            },
            "archiveImageAnalysis": archive_image_analysis.public_capability(),
            "voice": {
                **realtime_voice,
                "voiceType": self.settings.volcengine_voice_type,
                "realtimeResourceID": self.settings.volcengine_realtime_resource_id,
                "runtimeConfigEndpoint": "/voice/realtime-token",
            },
            "voiceClone": {
                "enabled": voice_clone_provider.is_configured,
                "provider": voice_clone_provider.provider_mode,
                "realProviderReady": voice_clone_provider.is_configured,
                # A configured voice provider alone is never permission to
                # train. The active profile path additionally requires a
                # server-side adult/liveness receipt from this independent
                # verifier capability.
                "identityEligibilityProviderReady": voice_identity_eligibility["ready"],
                "identityEligibilityProvider": voice_identity_eligibility["provider"],
                "trainingAdmissionEnabled": (
                    voice_training_admission_enabled
                ),
                "trainingAdmissionReason": voice_training_admission_reason,
                "trainingAdmissionContractVersion": voice_identity_eligibility["contractVersion"],
                "trainEndpoint": "/voice/profiles",
                "queryEndpoint": "/voice/profiles/{user_id}/{voice_profile_id}/refresh",
                "synthesisEndpoint": "/voice/synthesis",
                "synthesisProviderReady": voice_clone_tts_provider.is_configured,
                "requiresAuthorization": True,
                "qualityAcceptanceRequired": True,
                "defaultReleaseVisible": False,
                "speakerIdMode": self.settings.volcengine_voice_clone_speaker_id_mode,
                "consoleSpeakerIdConfigured": bool(self.settings.volcengine_voice_clone_speaker_id),
                "speakerIdPoolConfigured": bool(voice_clone_speaker_ids),
                "speakerIdPoolCount": len(voice_clone_speaker_ids),
                "speakerSlotAllocationMode": "exclusivePersistentSlot",
                "speakerSlotReusePolicy": "retireOnDelete",
                "logicalProfileIdSeparated": True,
                "modelType": self.settings.volcengine_voice_clone_model_type,
                "ttsResourceId": self.settings.volcengine_voice_clone_tts_resource_id,
                "voiceClone2TrialReady": (
                    voice_clone_provider.is_configured
                    and self.settings.volcengine_voice_clone_model_type == 5
                    and bool(voice_clone_speaker_ids)
                    and bool(self.settings.volcengine_voice_clone_tts_resource_id)
                ),
                "fallbackMode": "hiddenContract" if not voice_clone_provider.is_configured else "providerV3",
                "operationMatrix": voice_clone_operation_matrix,
                "lipSyncTimeline": {
                    "field": "visemeTimeline",
                    "source": "providerOptional",
                    "supported": False,
                    "fallbackMode": "avAudioPlayerMetering",
                    "contractVersion": 1,
                },
                "tencentAudioDrive": {
                    "supported": voice_clone_tts_provider.is_configured,
                    "synthesisEndpoint": "/voice/synthesis",
                    "requestOutputMode": "tencentAudioDrive",
                    "providerRequestFormat": "wav",
                    "audioFormat": "pcm16kMono",
                    "sampleRate": 16000,
                    "bitsPerSample": 16,
                    "channelCount": 1,
                    "fallbackMode": "providerTextDrive",
                    "contractVersion": 1,
                },
                "contractVersion": 3,
            },
            "digitalHuman": {
                **digital_human_access,
                "enabled": False,
                "reason": "productClosed",
                "productState": "closed",
                "providerMode": "blocked",
                "realProviderReady": False,
                "sdkProvider": "tencent-cloud-digital-human",
                "sdkAuthMode": "staticProjectCredentialUnsupportedOnMobile",
                "sdkAdapterLinked": False,
                "sdkReadinessMessage": "Tencent mobile SDK only exposes project-level static credentials; digital human rendering is blocked.",
                "sessionEndpoint": "/digital-human/sessions",
                "driveModes": ["streamText", "sendAudio"],
                "fallbackMode": "text",
                "assetMode": digital_human_asset_mode,
                "defaultReleaseVisible": False,
                "releaseVisible": False,
                "requiresBackendIssuedCredential": True,
                "credentialBroker": {
                    "required": True,
                    "status": digital_human_access["brokerStatus"],
                    "requiredProperties": ["scope", "ttl", "audience", "revocation"],
                    "verifiedProperties": [],
                    "missingProperties": ["scope", "ttl", "audience", "revocation"],
                },
                "sessionLease": {
                    "enabled": False,
                    "heartbeatEndpointTemplate": "/digital-human/sessions/{sessionId}/heartbeat",
                    "releaseEndpointTemplate": "/digital-human/sessions/{sessionId}/release",
                    "ttlSeconds": max(60, self.settings.tencent_digital_human_session_ttl_seconds),
                    "heartbeatIntervalSeconds": max(
                        10,
                        min(
                            self.settings.tencent_digital_human_heartbeat_interval_seconds,
                            max(60, self.settings.tencent_digital_human_session_ttl_seconds) // 2,
                        ),
                    ),
                    "maxConcurrentSessions": max(
                        1,
                        self.settings.tencent_digital_human_max_concurrent_sessions,
                    ),
                    "conflictStatusCode": 409,
                    "contractVersion": 1,
                },
            },
            "privacy": {
                "localOnly": "never_upload",
                "generationAllowed": "ai_and_backend_allowed",
                "familyCircle": "authorized_family_sync",
            },
        }

    def _voice_clone_speaker_ids(self) -> list[str]:
        return configured_voice_clone_speaker_ids(self.settings)

    def _digital_human_asset_mode(self) -> str:
        if self.settings.tencent_digital_human_asset_virtualman_key:
            return "asset"
        if self.settings.tencent_digital_human_virtualman_project_id:
            return "project"
        return "missing"

    def _capability_snapshots(
        self,
        *,
        archive_image_analysis: Any,
        identity_challenge: Dict[str, Any],
        provider_inventory: ProviderRuntimeInventory,
        release_policy: ReleasePolicyService,
    ) -> Dict[str, Dict[str, Any]]:
        composer = RuntimeCapabilityComposer()
        release_decisions = {
            feature: release_policy.build_snapshot(
                audience="owner",
                cohort="authenticatedOwner",
                client_build=release_policy.min_client_build,
                requested_feature=feature,
            ).features[0]
            for feature in (
                "archiveLocalAnalysis",
                "archiveAudioUpload",
                "archiveVideoUpload",
                "timeLetters",
                "echoDelayedReplies",
                "familyManagement",
                "familySpace",
                "voiceCloneShell",
                "digitalHumanLivePanel",
                "ownerMediaCaptureV1",
                "ownerMediaProcessingV1",
            )
        }

        image_enabled = archive_image_analysis.enabled
        image_provider_ready = image_enabled and archive_image_analysis.supports_vision
        media_storage = provider_inventory.status_for("ownerTruthMediaStorage")
        media_processing = provider_inventory.status_for("ownerTruthMediaProcessing")
        identity_provider = provider_inventory.status_for("identityChallenge")
        voice_provider = provider_inventory.status_for("voiceCloneShell")
        digital_human_provider = provider_inventory.status_for("digitalHumanLivePanel")

        inputs = (
            RuntimeCapabilityInput(
                capability="archiveImageAnalysis",
                implemented=True,
                enabled=image_enabled,
                provider_ready=image_provider_ready,
                release_visible=release_decisions["archiveLocalAnalysis"].releaseVisible,
                external_verified=False,
                provider=archive_image_analysis.provider_id,
                fallback_mode=archive_image_analysis.fallback_mode,
                reason=(
                    "providerVisionUnsupported"
                    if image_enabled and not archive_image_analysis.supports_vision
                    else "runtimeDisabled"
                    if not image_enabled
                    else "externalEvidenceMissing"
                ),
                provider_kind="imageAnalysis",
                operation="analyzeImage",
                data_class="ownerPrivateImage",
                region="providerManaged",
                retention_policy_version="archiveAnalysis-v1",
                configuration_status="valid" if image_enabled else "disabled",
                evidence_status="notVerified" if image_enabled else "notRequested",
            ),
            RuntimeCapabilityInput(
                capability="archiveAudioUpload",
                implemented=True,
                enabled=True,
                provider_ready=False,
                release_visible=release_decisions["archiveAudioUpload"].releaseVisible,
                external_verified=False,
                provider="mockObjectStorage",
                fallback_mode="metadataOnly",
                reason="mockProviderOnly",
                provider_kind="legacyMetadataSync",
                operation="syncMediaMetadata",
                data_class="localAudioMetadata",
                region="deviceLocal",
                retention_policy_version="archiveHiddenMedia-v1",
                configuration_status="mockOnly",
                evidence_status="notApplicable",
            ),
            RuntimeCapabilityInput(
                capability="archiveVideoUpload",
                implemented=True,
                enabled=True,
                provider_ready=False,
                release_visible=release_decisions["archiveVideoUpload"].releaseVisible,
                external_verified=False,
                provider="mockObjectStorage",
                fallback_mode="metadataOnly",
                reason="mockProviderOnly",
                provider_kind="legacyMetadataSync",
                operation="syncMediaMetadata",
                data_class="localVideoMetadata",
                region="deviceLocal",
                retention_policy_version="archiveHiddenMedia-v1",
                configuration_status="mockOnly",
                evidence_status="notApplicable",
            ),
            self._provider_input(
                status=media_storage,
                release_visible=release_decisions["ownerMediaCaptureV1"].releaseVisible,
            ),
            self._provider_input(
                status=media_processing,
                release_visible=release_decisions["ownerMediaProcessingV1"].releaseVisible,
            ),
            self._provider_input(
                status=identity_provider,
                release_visible=bool(identity_challenge.get("clientFlowEnabled", False)),
            ),
            RuntimeCapabilityInput(
                capability="timeLetters",
                implemented=True,
                enabled=False,
                provider_ready=False,
                release_visible=release_decisions["timeLetters"].releaseVisible,
                external_verified=False,
                provider="internalScheduler",
                fallback_mode="disabled",
                reason="productClosed",
                provider_kind="inAppDeliveryScheduler",
                operation="deliverTimeLetterReminder",
                data_class="timeLetterMetadata",
                region="serviceManaged",
                retention_policy_version="timeLetterRetention-v1",
                configuration_status="productClosed",
                evidence_status="notRequested",
            ),
            RuntimeCapabilityInput(
                capability="echoDelayedReplies",
                implemented=True,
                enabled=False,
                provider_ready=False,
                release_visible=release_decisions["echoDelayedReplies"].releaseVisible,
                external_verified=False,
                provider="internalScheduler",
                fallback_mode="disabled",
                reason="productClosed",
                provider_kind="inAppDeliveryScheduler",
                operation="deliverEchoDelayedReply",
                data_class="echoDelayedReplyMetadata",
                region="serviceManaged",
                retention_policy_version="echoDelayedReplyRetention-v1",
                configuration_status="productClosed",
                evidence_status="notRequested",
            ),
            RuntimeCapabilityInput(
                capability="familyManagement",
                implemented=True,
                enabled=True,
                provider_ready=True,
                release_visible=release_decisions["familyManagement"].releaseVisible,
                external_verified=True,
                evidence_timestamp=composer.now,
                provider="internalFamilyService",
                fallback_mode="authenticatedOwnerOnly",
                reason="ready",
                provider_kind="familyRelationshipService",
                operation="manageFamilyRelationship",
                data_class="familyRelationshipMetadata",
                region="serviceManaged",
                retention_policy_version="familyRelationshipRetention-v1",
                configuration_status="valid",
                evidence_status="internalServiceVerified",
            ),
            RuntimeCapabilityInput(
                capability="familySpace",
                implemented=True,
                enabled=True,
                provider_ready=True,
                release_visible=release_decisions["familySpace"].releaseVisible,
                external_verified=True,
                evidence_timestamp=composer.now,
                provider="internalPersonaService",
                fallback_mode="ownerOnly",
                reason="ready",
                provider_kind="personaService",
                operation="resolveOwnerPersona",
                data_class="ownerPersonaMetadata",
                region="serviceManaged",
                retention_policy_version="personaRetention-v1",
                configuration_status="valid",
                evidence_status="internalServiceVerified",
            ),
            self._provider_input(
                status=voice_provider,
                release_visible=release_decisions["voiceCloneShell"].releaseVisible,
            ),
            self._provider_input(
                status=digital_human_provider,
                release_visible=release_decisions["digitalHumanLivePanel"].releaseVisible,
            ),
        )
        return {
            item.capability: item.model_dump(mode="json")
            for item in (composer.compose(value) for value in inputs)
        }

    def _provider_input(
        self,
        *,
        status: ProviderRuntimeStatus,
        release_visible: bool,
    ) -> RuntimeCapabilityInput:
        control = self._control_decision(status.capability)
        return RuntimeCapabilityInput(
            capability=status.capability,
            implemented=True,
            enabled=status.enabled,
            provider_ready=(
                status.provider_ready
                if control is None
                else status.provider_ready and control.operational_ready
            ),
            release_visible=release_visible,
            external_verified=False,
            provider=status.provider,
            fallback_mode=status.fallback_mode,
            reason=(status.reason if control is None or control.operational_ready else control.reason),
            provider_kind=status.provider_kind,
            operation=status.operation,
            data_class=status.data_class,
            region=status.region,
            retention_policy_version=status.retention_policy_version,
            configuration_status=status.configuration_status,
            evidence_status=status.evidence_status,
            control_state=("legacy" if control is None else control.state.value),
            readiness_epoch=(None if control is None else control.readiness_epoch),
            readiness_observed_at=(None if control is None else control.observed_at),
            readiness_expires_at=(None if control is None else control.expires_at),
        )

    def _control_decision(
        self,
        capability: str,
    ) -> Optional[RuntimeCapabilityControlDecision]:
        if self.capability_control_registry is None:
            return None
        return self.capability_control_registry.decision(capability)

    def _provider_operational_ready(self, status: ProviderRuntimeStatus) -> bool:
        control = self._control_decision(status.capability)
        return bool(
            status.enabled
            and status.provider_ready
            and (control is None or control.operational_ready)
        )

    def _capability_control_descriptor(self) -> Dict[str, object]:
        if self.capability_control_registry is None:
            return {"contractVersion": 1, "capabilities": {}}
        return self.capability_control_registry.public_descriptor()
