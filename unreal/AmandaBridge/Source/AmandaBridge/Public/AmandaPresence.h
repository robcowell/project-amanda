// Project Amanda -- presence: the behaviour the orchestrator does not own.
//
// The mouth is solved from audio by MetaHuman's own speech solver. Everything
// above it -- blink, gaze, breath, drift -- is this. Without it the character
// is a talking head: articulate at the mouth and dead everywhere else, which is
// the failure build plan 17 and 18 are both circling.
//
// This is a port of `avatar-orchestrator/src/amanda/presence/schedulers.py`,
// which is the reference implementation and has the tests. Where a constant or
// a distribution looks arbitrary here it is not: the reasoning is in that file
// and was tuned against the previsualiser. Keep the two in step.
//
// Why the renderer owns this rather than the orchestrator streaming a face
// pose: presence runs at frame rate and must keep running when the orchestrator
// is restarted mid-conversation. The orchestrator sends *direction* -- a gaze
// target, a performance preset -- and the renderer owns the timing. Protocol v1
// makes `hold_ms` advisory for exactly this reason.

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "AmandaBridgeTypes.h"

#include "AmandaPresence.generated.h"

/** Everything needed to drive one frame of the face, above the mouth. */
USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaFaceState
{
	GENERATED_BODY()

	/** Eye aim in degrees, relative to looking straight at the user. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float EyeYaw = 0.0f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float EyePitch = 0.0f;

	/** Head aim, which lags the eyes and takes a fraction of the angle. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float HeadYaw = 0.0f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float HeadPitch = 0.0f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float HeadRoll = 0.0f;

	/** 1.0 fully open, 0.0 fully closed. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float LidOpen = 1.0f;

	/** Chest expansion, 0.0 empty to 1.0 full. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float Breath = 0.0f;

	/** The performance the director last asked for, and how strongly. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	EAmandaPreset Preset = EAmandaPreset::NeutralAttentive;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float Intensity = 0.0f;

	/** Where the gaze controller is currently aiming. Diagnostics, mostly. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	EAmandaGazeTarget GazeTarget = EAmandaGazeTarget::User;
};

/**
 * Blink. Independent of speech (build plan 18).
 *
 * Rate responds to cognitive state -- people blink less while concentrating --
 * but never to sentence boundaries, which is what reads as animation.
 */
struct FAmandaBlinkScheduler
{
	float IntervalLow = 2.4f;
	float IntervalHigh = 6.8f;
	float CloseMs = 62.0f;
	float OpenMs = 118.0f;
	float DoubleBlinkProbability = 0.12f;
	float SaccadeBlinkProbability = 0.22f;

	void Start(float Now, FRandomStream& Rng);
	/** Above 1.0 means longer gaps: less blinking, more concentration. */
	void SetRateScale(float Scale);
	/** A large saccade often carries a blink. Never on small corrections. */
	void OnGazeShift(float Now, float MagnitudeDeg, FRandomStream& Rng);
	/** Lid openness: 1.0 open, 0.0 closed. */
	float Update(float Now, FRandomStream& Rng);

private:
	void Trigger(float Now);
	void Schedule(float Now, FRandomStream& Rng);

	float NextAt = 0.0f;
	float BlinkStarted = 0.0f;
	bool bBlinking = false;
	int32 Queued = 0;
	float RateScale = 1.0f;
	bool bStarted = false;
};

/**
 * Eyes and head aim (build plan 8).
 *
 * Micro-saccades, so the eyes never sit perfectly still even on a held target;
 * and head lag, so the eyes arrive first and the head follows with a fraction
 * of the angle. Moving them in lockstep is one of the most reliable ways to
 * look synthetic.
 */
struct FAmandaGazeScheduler
{
	float ShiftLow = 1.2f;
	float ShiftHigh = 4.5f;
	float SaccadeLow = 0.35f;
	float SaccadeHigh = 1.6f;
	float SaccadeAmplitude = 1.4f;
	float EyeSpeed = 22.0f;
	float HeadFollow = 0.34f;
	float HeadSpeed = 3.2f;

	void Start(float Now, FRandomStream& Rng);
	/** Honour an orchestrator request, then resume self-scheduling. */
	void Command(float Now, EAmandaGazeTarget InTarget, int32 HoldMs, FRandomStream& Rng);
	void SetEyeContact(float Fraction);
	void Update(float Now, float DeltaSeconds, FRandomStream& Rng);

	FVector2D Eye() const { return EyeAngles; }
	FVector2D Head() const { return HeadAngles; }
	EAmandaGazeTarget Current() const { return Target; }
	/** Magnitude of a shift since the last call, then zero. One-shot. */
	float ConsumeShift();

private:
	void SetTarget(float Now, EAmandaGazeTarget InTarget);
	float DrawHold(FRandomStream& Rng) const;
	EAmandaGazeTarget ChooseTarget(FRandomStream& Rng) const;

	EAmandaGazeTarget Target = EAmandaGazeTarget::User;
	float EyeContact = 0.7f;
	FVector2D Aim = FVector2D::ZeroVector;
	FVector2D EyeAngles = FVector2D::ZeroVector;
	FVector2D HeadAngles = FVector2D::ZeroVector;
	FVector2D Offset = FVector2D::ZeroVector;
	float NextShiftAt = 0.0f;
	float NextSaccadeAt = 0.0f;
	float LastShiftMagnitude = 0.0f;
	bool bStarted = false;
};

/** Chest and shoulders. Inhale quicker than exhale; the period wanders. */
struct FAmandaBreathScheduler
{
	float Period = 4.2f;
	float PeriodJitter = 0.5f;
	float InhaleFraction = 0.38f;

	void Start(float Now, FRandomStream& Rng);
	float Update(float Now, FRandomStream& Rng);

private:
	float CycleStarted = 0.0f;
	float CurrentPeriod = 4.2f;
	bool bStarted = false;
};

/**
 * Microscopic head drift that never repeats.
 *
 * Three sines whose frequencies are in irrational ratios, so the sum has no
 * period. A single sine, or several with rational ratios, gives a loop the eye
 * finds within a minute -- the failure the sixty-second stillness test catches.
 */
struct FAmandaDriftScheduler
{
	float Amplitude = 0.9f;

	void Start(FRandomStream& Rng);
	/** (yaw, pitch, roll) offsets in degrees. */
	FVector Update(float Now);

private:
	FVector Phases = FVector::ZeroVector;
	FVector Rates = FVector::ZeroVector;
	bool bStarted = false;
};

/**
 * Presence, as an actor component.
 *
 * Attach it to the MetaHuman. It subscribes to the bridge, keeps the schedulers
 * running, and publishes a `FAmandaFaceState` every frame for the animation
 * graph to read. It deliberately never touches the mouth: that is solved from
 * audio and this has no business overriding it.
 */
UCLASS(ClassGroup = (Amanda), meta = (BlueprintSpawnableComponent), DisplayName = "Amanda Presence")
class AMANDABRIDGE_API UAmandaPresenceComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UAmandaPresenceComponent();

	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void TickComponent(float DeltaTime, ELevelTick TickType,
		FActorComponentTickFunction* ThisTickFunction) override;

	/** This frame's presence. Read it from the animation graph. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	FAmandaFaceState FaceState;

	/** Head aim as a rotator, for feeding into a head control. */
	UFUNCTION(BlueprintPure, Category = "Amanda|Presence")
	FRotator GetHeadRotation() const;

	/**
	 * Deterministic when set, which is what makes a bug reproducible. Zero
	 * seeds from the clock, which is what stops two characters in a scene
	 * blinking in unison.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Presence")
	int32 RandomSeed = 0;

	/** Follow the orchestrator's direction, when a bridge is connected. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Presence")
	bool bFollowBridge = true;

	/** Eye contact each conversation state asks for (build plan 8). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Presence")
	float ListeningEyeContact = 0.80f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Presence")
	float ThinkingEyeContact = 0.25f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Presence")
	float SpeakingEyeContact = 0.60f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Presence")
	float IdleEyeContact = 0.15f;

	// -- Bridge handlers -------------------------------------------------- //

	UFUNCTION()
	void HandleGaze(FAmandaGaze Gaze);

	UFUNCTION()
	void HandleThinking(bool bThinking);

	UFUNCTION()
	void HandleUserSpeech(bool bSpeaking, int32 DurationMs);

	UFUNCTION()
	void HandleSpeech(EAmandaSpeechPhase Phase, FAmandaSpeech Speech);

	UFUNCTION()
	void HandlePerformance(FAmandaPerformanceUpdate Update);

	UFUNCTION()
	void HandleReset();

private:
	void Subscribe();
	float Now() const;

	FRandomStream Rng;

	FAmandaBlinkScheduler Blink;
	FAmandaGazeScheduler Gaze;
	FAmandaBreathScheduler Breath;
	FAmandaDriftScheduler Drift;

	bool bSubscribed = false;
};
