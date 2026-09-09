// Project Amanda -- protocol v1 decoding.
//
// Deliberately free of any socket, subsystem or engine state, so the automation
// tests in Private/Tests can exercise it directly. This is the same split the
// Python side uses: protocol.py knows nothing about websocket.py.

#pragma once

#include "AmandaBridgeTypes.h"
#include "CoreMinimal.h"

/** The fifteen event names. Compared as strings so unknown ones can pass through. */
namespace AmandaEvents
{
	extern AMANDABRIDGE_API const TCHAR* SessionStarted;
	extern AMANDABRIDGE_API const TCHAR* SessionEnded;
	extern AMANDABRIDGE_API const TCHAR* UserDetected;
	extern AMANDABRIDGE_API const TCHAR* UserSpeechStarted;
	extern AMANDABRIDGE_API const TCHAR* UserSpeechEnded;
	extern AMANDABRIDGE_API const TCHAR* ThinkingStarted;
	extern AMANDABRIDGE_API const TCHAR* ThinkingEnded;
	extern AMANDABRIDGE_API const TCHAR* SpeechPrepare;
	extern AMANDABRIDGE_API const TCHAR* SpeechStarted;
	extern AMANDABRIDGE_API const TCHAR* SpeechCompleted;
	extern AMANDABRIDGE_API const TCHAR* SpeechCancelled;
	extern AMANDABRIDGE_API const TCHAR* PerformanceUpdate;
	extern AMANDABRIDGE_API const TCHAR* GazeSetTarget;
	extern AMANDABRIDGE_API const TCHAR* GestureTrigger;
	extern AMANDABRIDGE_API const TCHAR* AvatarReset;
}

/**
 * Decoding, with the tolerance a receiver is supposed to have.
 *
 * The Python reference implementation is strict because it is the *producer*:
 * an out-of-range coefficient there is a director bug worth failing on. This is
 * the *consumer*, where refusing a message only means the avatar misses a
 * direction and nobody learns anything. So the rules differ on purpose:
 *
 *   - Unrecognised event      -> carried through, `IsKnownEvent` says false.
 *   - Unrecognised field      -> ignored, per compatibility rule 1.
 *   - Unrecognised enum value -> Unknown, logged. Not fatal.
 *   - Out-of-range float      -> clamped, logged. Not fatal.
 *   - Wrong protocol version  -> refused. Nothing else can be trusted.
 *   - Missing required field  -> refused. A malformed message is a real bug.
 */
class AMANDABRIDGE_API FAmandaProtocol
{
public:
	/**
	 * Parse and validate one message's envelope.
	 *
	 * Validates the envelope only. The event name is not checked here, so the
	 * caller can decide to route or skip -- which is what lets a newer
	 * orchestrator drive an older renderer.
	 */
	static bool Decode(const FString& Raw, FAmandaEnvelope& OutEnvelope, FString& OutError);

	static bool IsKnownEvent(const FString& Event);

	static bool ParseSession(const FAmandaEnvelope& Envelope, FAmandaSession& Out, FString& OutError);
	static bool ParseUserPresence(const FAmandaEnvelope& Envelope, bool& bOutPresent, FString& OutError);
	static bool ParseUserSpeechEnded(const FAmandaEnvelope& Envelope, int32& OutDurationMs);
	static bool ParseSpeech(
		const FAmandaEnvelope& Envelope,
		EAmandaSpeechPhase Phase,
		FAmandaSpeech& Out,
		FString& OutError);
	static bool ParsePerformance(
		const FAmandaEnvelope& Envelope,
		FAmandaPerformanceUpdate& Out,
		FString& OutError);
	static bool ParseGaze(const FAmandaEnvelope& Envelope, FAmandaGaze& Out, FString& OutError);
	static bool ParseGesture(const FAmandaEnvelope& Envelope, FAmandaGesture& Out, FString& OutError);

	static EAmandaPreset ParsePreset(const FString& Value);
	static EAmandaGazeTarget ParseGazeTarget(const FString& Value);
	static EAmandaCancelReason ParseCancelReason(const FString& Value);

	/**
	 * A preset this build can actually animate.
	 *
	 * Falls back to NeutralAttentive rather than doing nothing, so a preset
	 * added to the orchestrator before it is authored here degrades to a calm
	 * face instead of a frozen one.
	 */
	static EAmandaPreset Animatable(EAmandaPreset Preset);
};
