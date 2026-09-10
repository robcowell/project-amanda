// Project Amanda -- presence, ported from the Python reference implementation.
//
// Every constant here has a counterpart in
// `avatar-orchestrator/src/amanda/presence/schedulers.py`, and the reasoning
// lives there. This file's job is to be a faithful port, not an improvement.

#include "AmandaPresence.h"
#include "AmandaBridgeSubsystem.h"

#include "Engine/GameInstance.h"
#include "Engine/World.h"

namespace
{
	/**
	 * A right-skewed interval within [Low, High].
	 *
	 * Uniform intervals are the classic tell: they produce a suspiciously even
	 * rhythm because short and long gaps are equally likely. Real inter-blink
	 * and inter-saccade times cluster low with an occasional long tail, so a
	 * log-normal shape reads as human where a flat one does not.
	 */
	float LogNormalIn(FRandomStream& Rng, float Low, float High)
	{
		const float Span = High - Low;
		const float Mu = FMath::Loge(Span * 0.42f);

		for (int32 Attempt = 0; Attempt < 8; ++Attempt)
		{
			// Box-Muller, because FRandomStream has no normal variate.
			const float U1 = FMath::Max(Rng.GetFraction(), SMALL_NUMBER);
			const float U2 = Rng.GetFraction();
			const float Normal = FMath::Sqrt(-2.0f * FMath::Loge(U1))
				* FMath::Cos(2.0f * PI * U2);
			const float Value = Low + FMath::Exp(Mu + 0.55f * Normal);
			if (Value <= High)
			{
				return Value;
			}
		}
		return Low + Span * 0.5f;
	}

	float EaseOut(float T)
	{
		T = FMath::Clamp(T, 0.0f, 1.0f);
		return 1.0f - (1.0f - T) * (1.0f - T);
	}

	float EaseInOut(float T)
	{
		T = FMath::Clamp(T, 0.0f, 1.0f);
		return T * T * (3.0f - 2.0f * T);
	}

	/** Exponential approach, frame-rate independent. */
	FVector2D Approach(const FVector2D& Current, const FVector2D& Goal, float Rate)
	{
		const float Factor = 1.0f - FMath::Exp(-FMath::Max(0.0f, Rate));
		return Current + (Goal - Current) * Factor;
	}

	/** Where each target sits, in degrees of (yaw, pitch). */
	FVector2D GazeAngles(EAmandaGazeTarget Target)
	{
		switch (Target)
		{
		case EAmandaGazeTarget::SlightlyLeft:     return FVector2D(-11.0f, 1.0f);
		case EAmandaGazeTarget::SlightlyRight:    return FVector2D(11.0f, 1.0f);
		case EAmandaGazeTarget::Down:             return FVector2D(-2.0f, -9.0f);
		case EAmandaGazeTarget::Distant:          return FVector2D(4.0f, 3.0f);
		case EAmandaGazeTarget::ObjectOfInterest: return FVector2D(-16.0f, -4.0f);
		case EAmandaGazeTarget::User:
		default:                                  return FVector2D::ZeroVector;
		}
	}

	/**
	 * Relative likelihood of each target when the controller chooses for
	 * itself. USER is handled separately: its weight comes from the
	 * eye-contact fraction the conversation state asks for.
	 */
	const TArray<TPair<EAmandaGazeTarget, float>>& IdleWeights()
	{
		static const TArray<TPair<EAmandaGazeTarget, float>> Weights = {
			{ EAmandaGazeTarget::SlightlyLeft,  1.0f },
			{ EAmandaGazeTarget::SlightlyRight, 1.0f },
			{ EAmandaGazeTarget::Down,          0.7f },
			{ EAmandaGazeTarget::Distant,       1.3f },
		};
		return Weights;
	}
}

// --------------------------------------------------------------------------- //
// Blink
// --------------------------------------------------------------------------- //

void FAmandaBlinkScheduler::Start(float InNow, FRandomStream& Rng)
{
	bStarted = true;
	Schedule(InNow, Rng);
}

void FAmandaBlinkScheduler::SetRateScale(float Scale)
{
	RateScale = FMath::Clamp(Scale, 0.25f, 4.0f);
}

void FAmandaBlinkScheduler::OnGazeShift(float InNow, float MagnitudeDeg, FRandomStream& Rng)
{
	if (MagnitudeDeg < 6.0f || bBlinking)
	{
		return;
	}
	if (Rng.GetFraction() < SaccadeBlinkProbability)
	{
		Trigger(InNow);
	}
}

float FAmandaBlinkScheduler::Update(float InNow, FRandomStream& Rng)
{
	if (!bStarted)
	{
		Start(InNow, Rng);
	}

	if (!bBlinking && InNow >= NextAt)
	{
		if (Rng.GetFraction() < DoubleBlinkProbability)
		{
			Queued = 1;
		}
		Trigger(InNow);
	}

	if (!bBlinking)
	{
		return 1.0f;
	}

	const float ElapsedMs = (InNow - BlinkStarted) * 1000.0f;
	if (ElapsedMs < CloseMs)
	{
		return 1.0f - EaseOut(ElapsedMs / CloseMs);
	}
	if (ElapsedMs < CloseMs + OpenMs)
	{
		return EaseOut((ElapsedMs - CloseMs) / OpenMs);
	}

	bBlinking = false;
	if (Queued > 0)
	{
		--Queued;
		Trigger(InNow + 0.04f);
	}
	else
	{
		Schedule(InNow, Rng);
	}
	return 1.0f;
}

void FAmandaBlinkScheduler::Trigger(float InNow)
{
	BlinkStarted = InNow;
	bBlinking = true;
}

void FAmandaBlinkScheduler::Schedule(float InNow, FRandomStream& Rng)
{
	NextAt = InNow + LogNormalIn(Rng, IntervalLow, IntervalHigh) * RateScale;
}

// --------------------------------------------------------------------------- //
// Gaze
// --------------------------------------------------------------------------- //

void FAmandaGazeScheduler::Start(float InNow, FRandomStream& Rng)
{
	bStarted = true;
	Aim = GazeAngles(Target);
	EyeAngles = Aim;
	NextShiftAt = InNow + LogNormalIn(Rng, ShiftLow, ShiftHigh);
	NextSaccadeAt = InNow + LogNormalIn(Rng, SaccadeLow, SaccadeHigh);
}

void FAmandaGazeScheduler::Command(float InNow, EAmandaGazeTarget InTarget, int32 HoldMs,
	FRandomStream& Rng)
{
	SetTarget(InNow, InTarget);
	// hold_ms of 0 means "until told otherwise", which here means "until the
	// scheduler's own next shift" -- the renderer keeps owning the timing.
	NextShiftAt = InNow + (HoldMs > 0 ? HoldMs / 1000.0f : DrawHold(Rng));
}

void FAmandaGazeScheduler::SetEyeContact(float Fraction)
{
	EyeContact = FMath::Clamp(Fraction, 0.0f, 1.0f);
}

void FAmandaGazeScheduler::Update(float InNow, float DeltaSeconds, FRandomStream& Rng)
{
	if (!bStarted)
	{
		Start(InNow, Rng);
	}

	if (InNow >= NextShiftAt)
	{
		SetTarget(InNow, ChooseTarget(Rng));
		NextShiftAt = InNow + DrawHold(Rng);
	}

	if (InNow >= NextSaccadeAt)
	{
		Offset = FVector2D(
			Rng.FRandRange(-SaccadeAmplitude, SaccadeAmplitude),
			Rng.FRandRange(-SaccadeAmplitude * 0.6f, SaccadeAmplitude * 0.6f));
		NextSaccadeAt = InNow + LogNormalIn(Rng, SaccadeLow, SaccadeHigh);
	}

	EyeAngles = Approach(EyeAngles, Aim + Offset, EyeSpeed * DeltaSeconds);
	HeadAngles = Approach(HeadAngles, EyeAngles * HeadFollow, HeadSpeed * DeltaSeconds);
}

float FAmandaGazeScheduler::ConsumeShift()
{
	const float Magnitude = LastShiftMagnitude;
	LastShiftMagnitude = 0.0f;
	return Magnitude;
}

void FAmandaGazeScheduler::SetTarget(float InNow, EAmandaGazeTarget InTarget)
{
	const FVector2D Previous = GazeAngles(Target);
	Target = InTarget;
	Aim = GazeAngles(InTarget);
	LastShiftMagnitude = FVector2D::Distance(Previous, Aim);
}

float FAmandaGazeScheduler::DrawHold(FRandomStream& Rng) const
{
	// Eye contact is a fraction of *time*, so it belongs here as much as in the
	// choice of target: looking at someone means resting on them longer, not
	// returning to them more often.
	const float Base = LogNormalIn(Rng, ShiftLow, ShiftHigh);
	return Target == EAmandaGazeTarget::User
		? Base * (1.0f + 1.6f * EyeContact)
		: Base * (1.3f - 0.5f * EyeContact);
}

EAmandaGazeTarget FAmandaGazeScheduler::ChooseTarget(FRandomStream& Rng) const
{
	// Never returns the current target: holding by re-picking the same place
	// would show up as a suspiciously long fixation.
	//
	// USER's weight is deliberately modest even at high eye contact. Weighting
	// it by contact/(1-contact) makes the sequence alternate user, away, user,
	// away almost perfectly, which is a visible pattern however random each
	// choice was. The time fraction is recovered in DrawHold instead.
	TArray<TPair<EAmandaGazeTarget, float>> Options;
	float TotalIdle = 0.0f;
	for (const TPair<EAmandaGazeTarget, float>& Entry : IdleWeights())
	{
		TotalIdle += Entry.Value;
		if (Entry.Key != Target)
		{
			Options.Add(Entry);
		}
	}
	if (Target != EAmandaGazeTarget::User)
	{
		Options.Add({ EAmandaGazeTarget::User, TotalIdle * (0.5f + EyeContact) });
	}
	if (Options.Num() == 0)
	{
		return EAmandaGazeTarget::User;
	}

	float Total = 0.0f;
	for (const TPair<EAmandaGazeTarget, float>& Option : Options)
	{
		Total += Option.Value;
	}

	float Roll = Rng.GetFraction() * Total;
	for (const TPair<EAmandaGazeTarget, float>& Option : Options)
	{
		Roll -= Option.Value;
		if (Roll <= 0.0f)
		{
			return Option.Key;
		}
	}
	return Options.Last().Key;
}

// --------------------------------------------------------------------------- //
// Breath and drift
// --------------------------------------------------------------------------- //

void FAmandaBreathScheduler::Start(float InNow, FRandomStream& Rng)
{
	bStarted = true;
	CycleStarted = InNow;
	CurrentPeriod = Period + Rng.FRandRange(-PeriodJitter, PeriodJitter);
}

float FAmandaBreathScheduler::Update(float InNow, FRandomStream& Rng)
{
	if (!bStarted)
	{
		Start(InNow, Rng);
	}

	float Phase = (InNow - CycleStarted) / CurrentPeriod;
	if (Phase >= 1.0f)
	{
		CycleStarted = InNow;
		CurrentPeriod = Period + Rng.FRandRange(-PeriodJitter, PeriodJitter);
		Phase = 0.0f;
	}

	return Phase < InhaleFraction
		? EaseInOut(Phase / InhaleFraction)
		: 1.0f - EaseInOut((Phase - InhaleFraction) / (1.0f - InhaleFraction));
}

void FAmandaDriftScheduler::Start(FRandomStream& Rng)
{
	bStarted = true;
	Phases = FVector(
		Rng.FRandRange(0.0f, 2.0f * PI),
		Rng.FRandRange(0.0f, 2.0f * PI),
		Rng.FRandRange(0.0f, 2.0f * PI));

	const float Base = Rng.FRandRange(0.055f, 0.085f);
	Rates = FVector(Base, Base * FMath::Sqrt(2.0f), Base * FMath::Sqrt(5.0f));
}

FVector FAmandaDriftScheduler::Update(float InNow)
{
	const float A = FMath::Sin(InNow * 2.0f * PI * Rates.X + Phases.X);
	const float B = FMath::Sin(InNow * 2.0f * PI * Rates.Y + Phases.Y);
	const float C = FMath::Sin(InNow * 2.0f * PI * Rates.Z + Phases.Z);

	return FVector(
		Amplitude * (A * 0.6f + C * 0.4f),
		Amplitude * 0.7f * (B * 0.7f + A * 0.3f),
		Amplitude * 0.4f * C);
}

// --------------------------------------------------------------------------- //
// Component
// --------------------------------------------------------------------------- //

UAmandaPresenceComponent::UAmandaPresenceComponent()
{
	PrimaryComponentTick.bCanEverTick = true;
	PrimaryComponentTick.bStartWithTickEnabled = true;
}

void UAmandaPresenceComponent::BeginPlay()
{
	Super::BeginPlay();

	// A zero seed means "differ between runs and between characters". Two
	// avatars in a scene blinking in unison is worse than either blinking
	// wrongly.
	Rng.Initialize(RandomSeed != 0 ? RandomSeed : FMath::Rand());

	Blink.IntervalLow = FMath::Max(0.5f, (float)BlinkIntervalSeconds.X);
	Blink.IntervalHigh = FMath::Max(Blink.IntervalLow + 0.5f, (float)BlinkIntervalSeconds.Y);

	const float Start = Now();
	Blink.Start(Start, Rng);
	Gaze.Start(Start, Rng);
	Breath.Start(Start, Rng);
	Drift.Start(Rng);

	Subscribe();
}

void UAmandaPresenceComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (bSubscribed)
	{
		if (const UGameInstance* GameInstance = GetWorld() ? GetWorld()->GetGameInstance() : nullptr)
		{
			if (UAmandaBridgeSubsystem* Bridge = GameInstance->GetSubsystem<UAmandaBridgeSubsystem>())
			{
				Bridge->OnGazeSetTarget.RemoveAll(this);
				Bridge->OnThinking.RemoveAll(this);
				Bridge->OnUserSpeech.RemoveAll(this);
				Bridge->OnSpeech.RemoveAll(this);
				Bridge->OnPerformanceUpdate.RemoveAll(this);
				Bridge->OnAvatarReset.RemoveAll(this);
			}
		}
		bSubscribed = false;
	}

	Super::EndPlay(EndPlayReason);
}

void UAmandaPresenceComponent::Subscribe()
{
	if (!bFollowBridge || bSubscribed)
	{
		return;
	}

	const UGameInstance* GameInstance = GetWorld() ? GetWorld()->GetGameInstance() : nullptr;
	UAmandaBridgeSubsystem* Bridge = GameInstance
		? GameInstance->GetSubsystem<UAmandaBridgeSubsystem>()
		: nullptr;
	if (Bridge == nullptr)
	{
		// No bridge is not an error: presence is autonomous by design and the
		// character should breathe and blink with no orchestrator at all.
		return;
	}

	Bridge->OnGazeSetTarget.AddDynamic(this, &UAmandaPresenceComponent::HandleGaze);
	Bridge->OnThinking.AddDynamic(this, &UAmandaPresenceComponent::HandleThinking);
	Bridge->OnUserSpeech.AddDynamic(this, &UAmandaPresenceComponent::HandleUserSpeech);
	Bridge->OnSpeech.AddDynamic(this, &UAmandaPresenceComponent::HandleSpeech);
	Bridge->OnPerformanceUpdate.AddDynamic(this, &UAmandaPresenceComponent::HandlePerformance);
	Bridge->OnAvatarReset.AddDynamic(this, &UAmandaPresenceComponent::HandleReset);
	bSubscribed = true;
}

float UAmandaPresenceComponent::Now() const
{
	const UWorld* World = GetWorld();
	return World ? World->GetTimeSeconds() : 0.0f;
}

void UAmandaPresenceComponent::TickComponent(float DeltaTime, ELevelTick TickType,
	FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	// Subscribing here as well as in BeginPlay: the subsystem may outlive a
	// level change, and a component that only ever tried once would stay deaf.
	Subscribe();

	const float T = Now();

	Gaze.Update(T, DeltaTime, Rng);
	// A large gaze shift often carries a blink, which is why the shift is
	// consumed rather than read: exposing it as a level made the blink fire on
	// every frame after a saccade.
	Blink.OnGazeShift(T, Gaze.ConsumeShift(), Rng);

	const FVector2D Eye = Gaze.Eye();
	const FVector2D Head = Gaze.Head();
	const FVector DriftOffset = Drift.Update(T);

	FaceState.EyeYaw = Eye.X;
	FaceState.EyePitch = Eye.Y;
	FaceState.HeadYaw = Head.X + DriftOffset.X;
	FaceState.HeadPitch = Head.Y + DriftOffset.Y;
	FaceState.HeadRoll = DriftOffset.Z;
	FaceState.LidOpen = Blink.Update(T, Rng);
	FaceState.Breath = Breath.Update(T, Rng);
	FaceState.GazeTarget = Gaze.Current();
}

FRotator UAmandaPresenceComponent::GetHeadRotation() const
{
	return FRotator(FaceState.HeadPitch, FaceState.HeadYaw, FaceState.HeadRoll);
}

// -- Bridge handlers -------------------------------------------------------- //

void UAmandaPresenceComponent::HandleGaze(FAmandaGaze InGaze)
{
	Gaze.Command(Now(), InGaze.Target, InGaze.HoldMs, Rng);
}

void UAmandaPresenceComponent::HandleThinking(bool bThinking)
{
	// Eye contact drops and the head goes still while considering (build plan
	// 7). People also blink less when concentrating, which is the rate scale.
	Gaze.SetEyeContact(bThinking ? ThinkingEyeContact : ListeningEyeContact);
	Blink.SetRateScale(bThinking ? 1.6f : 1.0f);
}

void UAmandaPresenceComponent::HandleUserSpeech(bool bSpeaking, int32 DurationMs)
{
	if (bSpeaking)
	{
		Gaze.SetEyeContact(ListeningEyeContact);
		Gaze.Command(Now(), EAmandaGazeTarget::User, 0, Rng);
	}
}

void UAmandaPresenceComponent::HandleSpeech(EAmandaSpeechPhase Phase, FAmandaSpeech Speech)
{
	switch (Phase)
	{
	case EAmandaSpeechPhase::Prepare:
		// The point of speech.prepare: bring the gaze back to the user *before*
		// the first sample plays, rather than snapping to attention on start.
		Gaze.SetEyeContact(SpeakingEyeContact);
		Gaze.Command(Now(), EAmandaGazeTarget::User, 0, Rng);
		break;
	case EAmandaSpeechPhase::Completed:
	case EAmandaSpeechPhase::Cancelled:
		Gaze.SetEyeContact(ListeningEyeContact);
		Blink.SetRateScale(1.0f);
		break;
	default:
		break;
	}
}

void UAmandaPresenceComponent::HandlePerformance(FAmandaPerformanceUpdate Update)
{
	FaceState.Preset = Update.Preset;
	FaceState.Intensity = Update.Intensity;

	// The director may override eye contact for a particular performance;
	// absent that, the conversation state's own fraction stands.
	if (Update.bHasEyeContact)
	{
		Gaze.SetEyeContact(Update.EyeContact);
	}
}

void UAmandaPresenceComponent::HandleReset()
{
	// Drop performance state and settle to neutral. The schedulers keep their
	// own timing: a reset is the orchestrator resynchronising, not the
	// character being restarted.
	FaceState.Preset = EAmandaPreset::NeutralAttentive;
	FaceState.Intensity = 0.0f;
	Gaze.SetEyeContact(IdleEyeContact);
	Blink.SetRateScale(1.0f);
}
