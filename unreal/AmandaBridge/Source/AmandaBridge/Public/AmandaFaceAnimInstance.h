// Project Amanda -- the join between presence and the face.
//
// The presence component computes blink, gaze, breath and drift. Something has
// to put those numbers onto the rig, and the obvious route -- editing the
// animation graph -- cannot be automated: graphs are authored by hand in the
// editor, and Python cannot reach them.
//
// This is the route that can. `UAnimInstance::AddCurveValue` sets a named curve
// on the evaluated pose directly, so a native parent class for the face's
// animation blueprint can push presence into the rig every frame without a
// single node being wired. `ABP_AmandaFace` is reparented onto this class, and
// keeps every graph Epic shipped in it.
//
// It layers *after* the Live Link pose, because NativeUpdateAnimation runs
// before the graph evaluates and the curve values it sets are applied to the
// result. Presence never writes a jaw or lip curve: the mouth is the speech
// solver's. The one exception is FAmandaCurveFloor below, which does not add
// a shape of its own -- it removes the solver's resting offset from a few
// mouth curves, so the mouth closes when she is silent.

#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimInstanceProxy.h"

#include "AmandaFaceAnimInstance.generated.h"

class UAmandaPresenceComponent;

/**
 * A curve's resting offset, to be removed.
 *
 * The speech solver never returns her mouth to closed. Measured in silence on
 * 2026-09-11, with the mood already Neutral: lips drawn apart (mouthLipsPull)
 * at ~0.38-0.45, the jaw open at ~0.16, the lower lip down at ~0.06 -- and
 * speech built on top of that, which Rob saw as "a little open-mouthed over
 * many phonemes". Each curve is remapped as (v - floor) / (1 - floor), clamped
 * to 0..1: silence reads as closed, and a full vowel still reaches 1.
 */
USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaCurveFloor
{
	GENERATED_BODY()

	FAmandaCurveFloor() = default;
	FAmandaCurveFloor(FName InCurve, float InFloor) : Curve(InCurve), Floor(InFloor) {}

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	FName Curve;

	/** The value the solver holds this curve at in silence. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves", meta = (ClampMin = 0.0, ClampMax = 0.9))
	float Floor = 0.0f;
};

/**
 * Curve names differ between MetaHuman versions and between rigs, so they are
 * configuration rather than constants. `UAmandaFaceCurveLibrary::ListCurveNames`
 * prints what a given mesh actually offers.
 */
USTRUCT(BlueprintType)
struct AMANDABRIDGE_API FAmandaFaceCurveNames
{
	GENERATED_BODY()

	/** Eyelid closure, 0 open to 1 shut. Usually one per eye. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> Blink;

	/** Eye aim, as four one-directional curves in the ARKit convention. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> LookLeft;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> LookRight;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> LookUp;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> LookDown;

	/**
	 * Head turn and tilt. The rig decomposes each into Down/Mid/Up variants for
	 * the face board's rows; the Mid ones are the plain rotation.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> TurnLeft;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> TurnRight;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> TurnUp;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> TurnDown;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> TiltLeft;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Curves")
	TArray<FName> TiltRight;
};

/**
 * Poses the head after the graph has finished with it.
 *
 * Curves were the first attempt and the rig ignores them; the blueprint's own
 * `ARKit_HeadRotation` was the second, and the graph ignores that too -- it was
 * written faithfully every frame while the head bone stayed bit-identical to
 * the reference pose, which is measured in `Scripts/probe_head_bone.py`. The
 * remaining honest route is to rotate the bone ourselves, last, where there is
 * nothing downstream left to overwrite it.
 *
 * A proxy is how a native anim instance reaches the evaluated pose without an
 * animation graph, which is the whole point of this class: none of this can be
 * authored from script, and hand-wiring a graph would not survive a rebuild of
 * the character.
 */
USTRUCT()
struct AMANDABRIDGE_API FAmandaFaceAnimProxy : public FAnimInstanceProxy
{
	GENERATED_BODY()

	FAmandaFaceAnimProxy() = default;
	explicit FAmandaFaceAnimProxy(UAnimInstance* InAnimInstance)
		: FAnimInstanceProxy(InAnimInstance)
	{
	}

	/** Game thread, before evaluation: copy what the worker thread will need. */
	virtual void PreUpdate(UAnimInstance* InAnimInstance, float DeltaSeconds) override;

	/**
	 * Worker thread: run the graph, *then* change its output.
	 *
	 * This evaluates the graph itself and returns true. The base implementation
	 * does not evaluate anything -- it returns false, and the engine runs the
	 * node graph afterwards (AnimInstanceProxy.cpp, EvaluateAnimation_WithRoot).
	 * The first version of this override called the base, changed the pose it
	 * got back, and returned false, so every change was made to a pose the graph
	 * then overwrote. That is why the rest-floor remap found none of its curves,
	 * and why a head turn applied here never reached the renderer.
	 */
	virtual bool Evaluate_WithRoot(FPoseContext& Output, FAnimNode_Base* InRootNode) override;

	/** Game thread again: report back whether the head was actually posed. */
	virtual void PostUpdate(UAnimInstance* InAnimInstance) const override;

private:
	/**
	 * Remap the solver's mouth curves so silence reads as closed.
	 *
	 * Here rather than anywhere later because curves are the face rig's input:
	 * this runs after the Live Link graph has produced them and before the
	 * post-process Control Rig turns them into bones.
	 */
	void RemoveRestFloors(FBlendedCurve& Curve) const;

	FRotator HeadRotation = FRotator::ZeroRotator;
	FName HeadBone = NAME_None;
	FName NeckBone = NAME_None;
	float NeckShare = 0.0f;
	bool bApply = false;
	/** Written during evaluation, read on the game thread by PostUpdate. */
	mutable bool bPosedHead = false;

	TArray<FAmandaCurveFloor> RestFloors;
	float RestFloorStrength = 0.0f;
	/** How many floored curves were present and remapped last evaluation. */
	mutable int32 RestFloorCurvesFound = 0;

	friend class UAmandaFaceAnimInstance;
};

/**
 * Parent class for the face's animation blueprint.
 *
 * Finds the presence component on the owning actor and writes its output to the
 * rig as curve values. Everything is configurable and nothing is required: with
 * no component, no curve names, or no presence at all, this does nothing and
 * the face behaves exactly as Epic's blueprint did.
 */
UCLASS(Blueprintable, DisplayName = "Amanda Face Anim Instance")
class AMANDABRIDGE_API UAmandaFaceAnimInstance : public UAnimInstance
{
	GENERATED_BODY()

public:
	virtual void NativeInitializeAnimation() override;
	virtual void NativeUpdateAnimation(float DeltaSeconds) override;

	/** Which curves to drive. Empty means "do not drive that part". */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	FAmandaFaceCurveNames CurveNames;

	/** Off switch, so the join can be A/B tested against a still face. */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	bool bApplyPresence = true;

	/**
	 * Which half of the job this instance does.
	 *
	 * The same class is the parent of two blueprints, because the two halves
	 * have to happen on opposite sides of the face's control rig. Curves are
	 * *input* to that rig, so they go in the main graph. Head rotation is a
	 * bone pose, and the rig overwrites every bone it touches -- measured:
	 * the head was turned in the main graph's output and arrived at the
	 * renderer bit-identical to the reference pose -- so it goes in the
	 * post-process graph, which runs after.
	 */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	bool bApplyCurves = true;

	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	bool bApplyHeadRotation = true;

	/**
	 * Degrees of eye aim that count as fully looking that way.
	 *
	 * The gaze scheduler works in degrees; the rig's look curves are 0..1. A
	 * MetaHuman eye travels about fifteen degrees before it looks strained.
	 */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	float EyeRangeDegrees = 15.0f;

	/**
	 * Degrees of head rotation that count as a full turn or tilt.
	 *
	 * Smaller than it sounds, because presence moves the head by a degree or
	 * two and dividing that by a generous range makes it invisible -- which is
	 * what "zero head tilt" looked like, before the head was driven at all.
	 */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	float HeadRangeDegrees = 6.0f;

	/** This frame's presence, for a graph that wants to read it. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float LidOpen = 1.0f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	float Breath = 0.0f;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	FRotator PresenceHeadRotation = FRotator::ZeroRotator;

	/** Whether a presence component was found. Diagnostics. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	bool bHasPresence = false;

	/**
	 * The bone the head rotation is applied to, and the one it borrows from.
	 *
	 * Splitting the angle across the neck is what stops it reading as a doll's
	 * head on a peg: real head turns start below the jaw.
	 */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	FName HeadBone = TEXT("head");

	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	FName NeckBone = TEXT("neck_02");

	/** Share of the angle the neck takes. The head takes the rest. */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	float NeckShare = 0.35f;

	/**
	 * How much of presence's head motion reaches the bone.
	 *
	 * Presence moves the head by a degree or two -- gaze lag takes a third of
	 * each glance, drift adds about a degree -- which is the reference
	 * implementation's number and stays in step with schedulers.py. Once the
	 * head finally moved on screen (2026-09-11), Rob called it "much improved
	 * but too subtle". Scaling here, on the renderer side, changes what the
	 * portrait shows without changing what presence means. Settable live.
	 *
	 * 2.5 by Rob's eye, from takes at 1, 2 and 3 of the same passage. Measured
	 * as the head's angle from rest: 1 gave a mean of 1.9 degrees, 2 gave 2.7,
	 * 3 gave 6.5 -- glances are randomly timed, so single takes are noisy.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Presence", meta = (ClampMin = 0.0, ClampMax = 5.0))
	float HeadMotionScale = 2.5f;

	/** Whether the head bone was found and posed. Diagnostics. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	bool bDrivingHead = false;

	/**
	 * The solver's resting offsets, removed so her mouth closes in silence.
	 *
	 * Defaults are the silence means measured under the Neutral mood on
	 * 2026-09-11. See FAmandaCurveFloor.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Speech")
	TArray<FAmandaCurveFloor> RestFloors = {
		FAmandaCurveFloor(FName(TEXT("CTRL_expressions_jawOpen")), 0.16f),
		FAmandaCurveFloor(FName(TEXT("CTRL_expressions_mouthLipsPullUL")), 0.38f),
		FAmandaCurveFloor(FName(TEXT("CTRL_expressions_mouthLipsPullUR")), 0.38f),
		FAmandaCurveFloor(FName(TEXT("CTRL_expressions_mouthLipsPullDL")), 0.45f),
		FAmandaCurveFloor(FName(TEXT("CTRL_expressions_mouthLipsPullDR")), 0.43f),
		FAmandaCurveFloor(FName(TEXT("CTRL_expressions_mouthLowerLipDepressL")), 0.05f),
		FAmandaCurveFloor(FName(TEXT("CTRL_expressions_mouthLowerLipDepressR")), 0.07f),
	};

	/** Off switch, so the change can be A/B'd live against the solver's own. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Speech")
	bool bRemoveRestFloor = true;

	/**
	 * How much of each floor to remove, 0..1 -- the one dial to turn by eye.
	 * Below 1 leaves some resting openness, if fully closed reads as clenched.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Amanda|Speech", meta = (ClampMin = 0.0, ClampMax = 1.0))
	float RestFloorStrength = 1.0f;

	/** How many floored curves the solver's output actually contained. Diagnostics. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Speech")
	int32 RestFloorCurvesFound = 0;

protected:
	virtual FAnimInstanceProxy* CreateAnimInstanceProxy() override;

private:
	void ApplyToCurves(const TArray<FName>& Names, float Value);

	UPROPERTY(Transient)
	TObjectPtr<UAmandaPresenceComponent> Presence = nullptr;

	friend struct FAmandaFaceAnimProxy;
};

/** Finding out what a rig actually calls things, rather than guessing. */
UCLASS()
class AMANDABRIDGE_API UAmandaFaceCurveLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Every curve name on a skeletal mesh's skeleton, optionally filtered.
	 *
	 * Written because the names could not be reached from Python and a printed
	 * wiring sheet that invents them would be worse than one that admits it
	 * does not know.
	 */
	UFUNCTION(BlueprintCallable, Category = "Amanda|Curves")
	static TArray<FName> ListCurveNames(USkeletalMesh* Mesh, const FString& Contains);
};
