// Project Amanda -- protocol v1 vocabulary, as Blueprint-visible types.
//
// Mirrors src/amanda/avatar/protocol.py. If the two ever disagree, that file
// and PROTOCOL.md are the specification and this is the bug.

#pragma once

#include "CoreMinimal.h"
#include "AmandaBridgeTypes.generated.h"

class FJsonObject;

/** The protocol version this build speaks. Anything else is refused. */
#define AMANDA_PROTOCOL_VERSION 1

/**
 * The restrained performance vocabulary (build plan 5.2).
 *
 * Unknown maps a preset this build was not written for. A renderer should fall
 * back to NeutralAttentive rather than doing nothing -- a newer orchestrator
 * naming a preset you have not authored is an expected condition, not an error.
 */
UENUM(BlueprintType)
enum class EAmandaPreset : uint8
{
	Unknown          UMETA(DisplayName = "Unknown"),
	NeutralAttentive UMETA(DisplayName = "Neutral Attentive"),
	Listening        UMETA(DisplayName = "Listening"),
	Considering      UMETA(DisplayName = "Considering"),
	MildlyAmused     UMETA(DisplayName = "Mildly Amused"),
	Warm             UMETA(DisplayName = "Warm"),
	Concerned        UMETA(DisplayName = "Concerned"),
	Uncertain        UMETA(DisplayName = "Uncertain"),
	Confused         UMETA(DisplayName = "Confused"),
	Surprised        UMETA(DisplayName = "Surprised"),
	Explaining       UMETA(DisplayName = "Explaining"),
	Enthusiastic     UMETA(DisplayName = "Enthusiastic"),
	Serious          UMETA(DisplayName = "Serious")
};

/** Where the eyes go (build plan 8). Angles stay on this side of the wire. */
UENUM(BlueprintType)
enum class EAmandaGazeTarget : uint8
{
	Unknown          UMETA(DisplayName = "Unknown"),
	User             UMETA(DisplayName = "User"),
	SlightlyLeft     UMETA(DisplayName = "Slightly Left"),
	SlightlyRight    UMETA(DisplayName = "Slightly Right"),
	Down             UMETA(DisplayName = "Down"),
	Distant          UMETA(DisplayName = "Distant"),
	ObjectOfInterest UMETA(DisplayName = "Object Of Interest")
};

/** Why speech stopped early. */
UENUM(BlueprintType)
enum class EAmandaCancelReason : uint8
{
	Unknown    UMETA(DisplayName = "Unknown"),
	BargeIn    UMETA(DisplayName = "Barge In"),
	Error      UMETA(DisplayName = "Error"),
	Shutdown   UMETA(DisplayName = "Shutdown"),
	Superseded UMETA(DisplayName = "Superseded")
};

/** Which point in an utterance's life a speech event marks. */
UENUM(BlueprintType)
enum class EAmandaSpeechPhase : uint8
{
	/** Audio is coming shortly. Return the gaze to the user now, not on Started. */
	Prepare   UMETA(DisplayName = "Prepare"),
	/** Playback has begun. The avatar's T6. */
	Started   UMETA(DisplayName = "Started"),
	Completed UMETA(DisplayName = "Completed"),
	Cancelled UMETA(DisplayName = "Cancelled")
};

// --------------------------------------------------------------------------
// Payloads
// --------------------------------------------------------------------------

USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaSession
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	FString SessionId;

	/** Only meaningful on session.ended, and optional even there. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	FString Reason;
};

USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaSpeech
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	FString UtteranceId;

	/** Present on Prepare. Unknown otherwise. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	EAmandaPreset Preset = EAmandaPreset::Unknown;

	/**
	 * Subtitles and debugging only. The orchestrator may omit it and the
	 * renderer must not depend on it.
	 */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	FString Text;

	/** Where to read the audio. Audio never travels over this protocol. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	FString AudioChannel = TEXT("stream");

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	int32 SampleRate = 24000;

	/** Zero while the utterance is still being synthesised. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	int32 DurationMs = 0;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	EAmandaCancelReason CancelReason = EAmandaCancelReason::Unknown;

	/** Keep this short. Barge-in must not feel like a media player fading out. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	int32 FadeMs = 80;
};

/**
 * A performance direction (build plan 5.3).
 *
 * Preset and Intensity are the whole contract. The per-region coefficients are
 * optional overrides -- an animation graph that only implements presets can
 * ignore them entirely, which is why each carries its own bHas flag rather than
 * defaulting to something that looks like a real value.
 */
USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaPerformanceUpdate
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	EAmandaPreset Preset = EAmandaPreset::NeutralAttentive;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	float Intensity = 0.f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	int32 TransitionMs = 450;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	bool bHasEyeContact = false;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	float EyeContact = 0.f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	bool bHasHeadMotion = false;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	float HeadMotion = 0.f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	bool bHasBrowActivity = false;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	float BrowActivity = 0.f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	bool bHasSmile = false;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	float Smile = 0.f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	bool bHasGestureProbability = false;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	float GestureProbability = 0.f;
};

USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaGaze
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	EAmandaGazeTarget Target = EAmandaGazeTarget::User;

	/**
	 * Advisory. The gaze controller owns stochastic timing and may hold longer
	 * or shorter; zero means "until told otherwise".
	 */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	int32 HoldMs = 0;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	int32 TransitionMs = 220;
};

USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaGesture
{
	GENERATED_BODY()

	/** Gesture names are defined by this side, not by the orchestrator. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	FString Gesture;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	float Intensity = 0.2f;
};

/**
 * One decoded message, before its payload has been interpreted.
 *
 * Event stays an FString rather than an enum so an unrecognised event can be
 * carried, logged and skipped. Turning it into an error here would break the
 * compatibility rule that lets a newer orchestrator drive an older renderer.
 */
USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaEnvelope
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	int32 Version = AMANDA_PROTOCOL_VERSION;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	FString Event;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda")
	double Timestamp = 0.0;

	/** Not Blueprint-visible: FJsonObject is not a USTRUCT. */
	TSharedPtr<FJsonObject> Payload;
};
