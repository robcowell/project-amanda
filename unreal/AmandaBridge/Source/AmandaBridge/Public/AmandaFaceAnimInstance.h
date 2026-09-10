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
	 * Degrees of eye aim that count as fully looking that way.
	 *
	 * The gaze scheduler works in degrees; the rig's look curves are 0..1. A
	 * MetaHuman eye travels about fifteen degrees before it looks strained.
	 */
	UPROPERTY(EditDefaultsOnly, BlueprintReadWrite, Category = "Amanda|Presence")
	float EyeRangeDegrees = 15.0f;

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

private:
	void ApplyToCurves(const TArray<FName>& Names, float Value);

	UPROPERTY(Transient)
	TObjectPtr<UAmandaPresenceComponent> Presence = nullptr;
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
