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
// result. The mouth is untouched: nothing here writes a jaw or lip curve, and
// nothing should.

#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimInstanceProxy.h"

#include "AmandaFaceAnimInstance.generated.h"

class UAmandaPresenceComponent;

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

	/** Worker thread: run the graph, then turn the head. */
	virtual bool Evaluate(FPoseContext& Output) override;

	/** Game thread again: report back whether the head was actually posed. */
	virtual void PostUpdate(UAnimInstance* InAnimInstance) const override;

private:
	FRotator HeadRotation = FRotator::ZeroRotator;
	FName HeadBone = NAME_None;
	FName NeckBone = NAME_None;
	float NeckShare = 0.0f;
	bool bApply = false;
	/** Written during evaluation, read on the game thread by PostUpdate. */
	mutable bool bPosedHead = false;

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

	/** Whether the head bone was found and posed. Diagnostics. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Presence")
	bool bDrivingHead = false;

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
