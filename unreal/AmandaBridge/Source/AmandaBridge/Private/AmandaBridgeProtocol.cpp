// Project Amanda -- protocol v1 decoding.

#include "AmandaBridgeProtocol.h"

#include "Dom/JsonObject.h"
#include "Logging/LogMacros.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY_STATIC(LogAmandaProtocol, Log, All);

namespace AmandaEvents
{
	const TCHAR* SessionStarted     = TEXT("session.started");
	const TCHAR* SessionEnded       = TEXT("session.ended");
	const TCHAR* UserDetected       = TEXT("user.detected");
	const TCHAR* UserSpeechStarted  = TEXT("user.speech_started");
	const TCHAR* UserSpeechEnded    = TEXT("user.speech_ended");
	const TCHAR* ThinkingStarted    = TEXT("assistant.thinking_started");
	const TCHAR* ThinkingEnded      = TEXT("assistant.thinking_ended");
	const TCHAR* SpeechPrepare      = TEXT("speech.prepare");
	const TCHAR* SpeechStarted      = TEXT("speech.started");
	const TCHAR* SpeechCompleted    = TEXT("speech.completed");
	const TCHAR* SpeechCancelled    = TEXT("speech.cancelled");
	const TCHAR* PerformanceUpdate  = TEXT("performance.update");
	const TCHAR* GazeSetTarget      = TEXT("gaze.set_target");
	const TCHAR* GestureTrigger     = TEXT("gesture.trigger");
	const TCHAR* AvatarReset        = TEXT("avatar.reset");
}

namespace
{
	/** Clamp rather than refuse: see the class comment on FAmandaProtocol. */
	float ReadUnit(
		const TSharedPtr<FJsonObject>& Payload,
		const FString& Field,
		float Fallback,
		bool& bOutPresent)
	{
		double Value = 0.0;
		bOutPresent = Payload.IsValid() && Payload->TryGetNumberField(Field, Value);
		if (!bOutPresent)
		{
			return Fallback;
		}
		if (Value < 0.0 || Value > 1.0)
		{
			UE_LOG(LogAmandaProtocol, Warning,
				TEXT("'%s' was %.3f, outside 0..1; clamping. This is an orchestrator bug."),
				*Field, Value);
			Value = FMath::Clamp(Value, 0.0, 1.0);
		}
		return static_cast<float>(Value);
	}

	int32 ReadDurationMs(const TSharedPtr<FJsonObject>& Payload, const FString& Field, int32 Fallback)
	{
		double Value = 0.0;
		if (!Payload.IsValid() || !Payload->TryGetNumberField(Field, Value))
		{
			return Fallback;
		}
		return FMath::Max(0, static_cast<int32>(Value));
	}

	bool RequireString(
		const TSharedPtr<FJsonObject>& Payload,
		const FString& Field,
		FString& Out,
		FString& OutError)
	{
		if (!Payload.IsValid() || !Payload->TryGetStringField(Field, Out))
		{
			OutError = FString::Printf(TEXT("missing required field '%s'"), *Field);
			return false;
		}
		return true;
	}
}

bool FAmandaProtocol::Decode(const FString& Raw, FAmandaEnvelope& OutEnvelope, FString& OutError)
{
	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Raw);
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
	{
		OutError = TEXT("not a JSON object");
		return false;
	}

	double Version = 0.0;
	if (!Root->TryGetNumberField(TEXT("version"), Version)
		|| static_cast<int32>(Version) != AMANDA_PROTOCOL_VERSION)
	{
		OutError = FString::Printf(
			TEXT("unsupported protocol version %g, expected %d"), Version, AMANDA_PROTOCOL_VERSION);
		return false;
	}

	FString Event;
	if (!Root->TryGetStringField(TEXT("event"), Event))
	{
		OutError = TEXT("missing required field 'event'");
		return false;
	}

	OutEnvelope.Version = static_cast<int32>(Version);
	OutEnvelope.Event = Event;
	OutEnvelope.Timestamp = 0.0;
	Root->TryGetNumberField(TEXT("timestamp"), OutEnvelope.Timestamp);

	// Payload is optional on the wire; an absent one behaves as an empty object
	// so every parser below can read it without a null check.
	const TSharedPtr<FJsonObject>* Payload = nullptr;
	OutEnvelope.Payload = Root->TryGetObjectField(TEXT("payload"), Payload)
		? *Payload
		: MakeShared<FJsonObject>();

	return true;
}

bool FAmandaProtocol::IsKnownEvent(const FString& Event)
{
	static const TSet<FString> Known = {
		AmandaEvents::SessionStarted,    AmandaEvents::SessionEnded,
		AmandaEvents::UserDetected,      AmandaEvents::UserSpeechStarted,
		AmandaEvents::UserSpeechEnded,   AmandaEvents::ThinkingStarted,
		AmandaEvents::ThinkingEnded,     AmandaEvents::SpeechPrepare,
		AmandaEvents::SpeechStarted,     AmandaEvents::SpeechCompleted,
		AmandaEvents::SpeechCancelled,   AmandaEvents::PerformanceUpdate,
		AmandaEvents::GazeSetTarget,     AmandaEvents::GestureTrigger,
		AmandaEvents::AvatarReset,
	};
	return Known.Contains(Event);
}

// --------------------------------------------------------------------------
// Payloads
// --------------------------------------------------------------------------

bool FAmandaProtocol::ParseSession(const FAmandaEnvelope& Envelope, FAmandaSession& Out, FString& OutError)
{
	if (!RequireString(Envelope.Payload, TEXT("session_id"), Out.SessionId, OutError))
	{
		return false;
	}
	Envelope.Payload->TryGetStringField(TEXT("reason"), Out.Reason);
	return true;
}

bool FAmandaProtocol::ParseUserPresence(const FAmandaEnvelope& Envelope, bool& bOutPresent, FString& OutError)
{
	if (!Envelope.Payload.IsValid() || !Envelope.Payload->TryGetBoolField(TEXT("present"), bOutPresent))
	{
		OutError = TEXT("missing required field 'present'");
		return false;
	}
	return true;
}

bool FAmandaProtocol::ParseUserSpeechEnded(const FAmandaEnvelope& Envelope, int32& OutDurationMs)
{
	OutDurationMs = ReadDurationMs(Envelope.Payload, TEXT("duration_ms"), 0);
	return true;
}

bool FAmandaProtocol::ParseSpeech(
	const FAmandaEnvelope& Envelope,
	EAmandaSpeechPhase Phase,
	FAmandaSpeech& Out,
	FString& OutError)
{
	if (!RequireString(Envelope.Payload, TEXT("utterance_id"), Out.UtteranceId, OutError))
	{
		return false;
	}

	const TSharedPtr<FJsonObject>& Payload = Envelope.Payload;

	if (Phase == EAmandaSpeechPhase::Prepare)
	{
		FString PresetName;
		if (Payload->TryGetStringField(TEXT("preset"), PresetName))
		{
			Out.Preset = ParsePreset(PresetName);
		}
		Payload->TryGetStringField(TEXT("text"), Out.Text);
	}
	else if (Phase == EAmandaSpeechPhase::Started)
	{
		if (!Payload->TryGetStringField(TEXT("audio_channel"), Out.AudioChannel))
		{
			Out.AudioChannel = TEXT("stream");
		}
		double SampleRate = 24000.0;
		Payload->TryGetNumberField(TEXT("sample_rate"), SampleRate);
		Out.SampleRate = FMath::Max(1, static_cast<int32>(SampleRate));
		Out.DurationMs = ReadDurationMs(Payload, TEXT("duration_ms"), 0);
	}
	else if (Phase == EAmandaSpeechPhase::Cancelled)
	{
		FString Reason;
		Out.CancelReason = Payload->TryGetStringField(TEXT("reason"), Reason)
			? ParseCancelReason(Reason)
			: EAmandaCancelReason::BargeIn;
		Out.FadeMs = ReadDurationMs(Payload, TEXT("fade_ms"), 80);
	}

	return true;
}

bool FAmandaProtocol::ParsePerformance(
	const FAmandaEnvelope& Envelope,
	FAmandaPerformanceUpdate& Out,
	FString& OutError)
{
	FString PresetName;
	if (!RequireString(Envelope.Payload, TEXT("preset"), PresetName, OutError))
	{
		return false;
	}

	const TSharedPtr<FJsonObject>& Payload = Envelope.Payload;

	bool bHasIntensity = false;
	const float Intensity = ReadUnit(Payload, TEXT("intensity"), 0.f, bHasIntensity);
	if (!bHasIntensity)
	{
		OutError = TEXT("missing required field 'intensity'");
		return false;
	}

	Out.Preset = ParsePreset(PresetName);
	Out.Intensity = Intensity;
	Out.TransitionMs = ReadDurationMs(Payload, TEXT("transition_ms"), 450);
	Out.EyeContact = ReadUnit(Payload, TEXT("eye_contact"), 0.f, Out.bHasEyeContact);
	Out.HeadMotion = ReadUnit(Payload, TEXT("head_motion"), 0.f, Out.bHasHeadMotion);
	Out.BrowActivity = ReadUnit(Payload, TEXT("brow_activity"), 0.f, Out.bHasBrowActivity);
	Out.Smile = ReadUnit(Payload, TEXT("smile"), 0.f, Out.bHasSmile);
	Out.GestureProbability =
		ReadUnit(Payload, TEXT("gesture_probability"), 0.f, Out.bHasGestureProbability);
	return true;
}

bool FAmandaProtocol::ParseGaze(const FAmandaEnvelope& Envelope, FAmandaGaze& Out, FString& OutError)
{
	FString TargetName;
	if (!RequireString(Envelope.Payload, TEXT("target"), TargetName, OutError))
	{
		return false;
	}
	Out.Target = ParseGazeTarget(TargetName);
	Out.HoldMs = ReadDurationMs(Envelope.Payload, TEXT("hold_ms"), 0);
	Out.TransitionMs = ReadDurationMs(Envelope.Payload, TEXT("transition_ms"), 220);
	return true;
}

bool FAmandaProtocol::ParseGesture(const FAmandaEnvelope& Envelope, FAmandaGesture& Out, FString& OutError)
{
	if (!RequireString(Envelope.Payload, TEXT("gesture"), Out.Gesture, OutError))
	{
		return false;
	}
	bool bUnused = false;
	Out.Intensity = ReadUnit(Envelope.Payload, TEXT("intensity"), 0.2f, bUnused);
	return true;
}

// --------------------------------------------------------------------------
// Enums
// --------------------------------------------------------------------------

EAmandaPreset FAmandaProtocol::ParsePreset(const FString& Value)
{
	static const TMap<FString, EAmandaPreset> Map = {
		{ TEXT("neutral_attentive"), EAmandaPreset::NeutralAttentive },
		{ TEXT("listening"),         EAmandaPreset::Listening },
		{ TEXT("considering"),       EAmandaPreset::Considering },
		{ TEXT("mildly_amused"),     EAmandaPreset::MildlyAmused },
		{ TEXT("warm"),              EAmandaPreset::Warm },
		{ TEXT("concerned"),         EAmandaPreset::Concerned },
		{ TEXT("uncertain"),         EAmandaPreset::Uncertain },
		{ TEXT("confused"),          EAmandaPreset::Confused },
		{ TEXT("surprised"),         EAmandaPreset::Surprised },
		{ TEXT("explaining"),        EAmandaPreset::Explaining },
		{ TEXT("enthusiastic"),      EAmandaPreset::Enthusiastic },
		{ TEXT("serious"),           EAmandaPreset::Serious },
	};
	if (const EAmandaPreset* Found = Map.Find(Value))
	{
		return *Found;
	}
	UE_LOG(LogAmandaProtocol, Warning, TEXT("unknown preset '%s'; falling back"), *Value);
	return EAmandaPreset::Unknown;
}

EAmandaGazeTarget FAmandaProtocol::ParseGazeTarget(const FString& Value)
{
	static const TMap<FString, EAmandaGazeTarget> Map = {
		{ TEXT("user"),               EAmandaGazeTarget::User },
		{ TEXT("slightly_left"),      EAmandaGazeTarget::SlightlyLeft },
		{ TEXT("slightly_right"),     EAmandaGazeTarget::SlightlyRight },
		{ TEXT("down"),               EAmandaGazeTarget::Down },
		{ TEXT("distant"),            EAmandaGazeTarget::Distant },
		{ TEXT("object_of_interest"), EAmandaGazeTarget::ObjectOfInterest },
	};
	if (const EAmandaGazeTarget* Found = Map.Find(Value))
	{
		return *Found;
	}
	UE_LOG(LogAmandaProtocol, Warning, TEXT("unknown gaze target '%s'"), *Value);
	return EAmandaGazeTarget::Unknown;
}

EAmandaCancelReason FAmandaProtocol::ParseCancelReason(const FString& Value)
{
	static const TMap<FString, EAmandaCancelReason> Map = {
		{ TEXT("barge_in"),   EAmandaCancelReason::BargeIn },
		{ TEXT("error"),      EAmandaCancelReason::Error },
		{ TEXT("shutdown"),   EAmandaCancelReason::Shutdown },
		{ TEXT("superseded"), EAmandaCancelReason::Superseded },
	};
	if (const EAmandaCancelReason* Found = Map.Find(Value))
	{
		return *Found;
	}
	return EAmandaCancelReason::Unknown;
}

EAmandaPreset FAmandaProtocol::Animatable(EAmandaPreset Preset)
{
	return Preset == EAmandaPreset::Unknown ? EAmandaPreset::NeutralAttentive : Preset;
}
