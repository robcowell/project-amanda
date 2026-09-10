// Project Amanda -- presence onto the rig, without touching the graph.

#include "AmandaFaceAnimInstance.h"
#include "AmandaPresence.h"

#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "GameFramework/Actor.h"

void UAmandaFaceAnimInstance::NativeInitializeAnimation()
{
	Super::NativeInitializeAnimation();

	if (const AActor* Owner = GetOwningActor())
	{
		Presence = Owner->FindComponentByClass<UAmandaPresenceComponent>();
	}
	bHasPresence = Presence != nullptr;
}

void UAmandaFaceAnimInstance::NativeUpdateAnimation(float DeltaSeconds)
{
	Super::NativeUpdateAnimation(DeltaSeconds);

	if (Presence == nullptr)
	{
		// The anim instance is also created for the asset preview, where there
		// is no actor and no component. Retrying rather than giving up: the
		// component may arrive a frame later than the animation.
		if (const AActor* Owner = GetOwningActor())
		{
			Presence = Owner->FindComponentByClass<UAmandaPresenceComponent>();
		}
		bHasPresence = Presence != nullptr;
		if (Presence == nullptr)
		{
			return;
		}
	}

	const FAmandaFaceState& Face = Presence->FaceState;
	LidOpen = Face.LidOpen;
	Breath = Face.Breath;
	PresenceHeadRotation = Presence->GetHeadRotation();

	if (!bApplyPresence)
	{
		return;
	}

	// Blink curves close the eye, so they are the inverse of openness. This is
	// the one that looks like a bug when it is wrong: the character sits with
	// its eyes serenely shut.
	ApplyToCurves(CurveNames.Blink, FMath::Clamp(1.0f - Face.LidOpen, 0.0f, 1.0f));

	// Eye aim arrives as signed degrees and the rig wants four one-directional
	// curves, each 0..1.
	const float Yaw = FMath::Clamp(Face.EyeYaw / FMath::Max(EyeRangeDegrees, 1.0f), -1.0f, 1.0f);
	const float Pitch = FMath::Clamp(Face.EyePitch / FMath::Max(EyeRangeDegrees, 1.0f), -1.0f, 1.0f);

	ApplyToCurves(CurveNames.LookLeft, FMath::Max(0.0f, -Yaw));
	ApplyToCurves(CurveNames.LookRight, FMath::Max(0.0f, Yaw));
	ApplyToCurves(CurveNames.LookUp, FMath::Max(0.0f, Pitch));
	ApplyToCurves(CurveNames.LookDown, FMath::Max(0.0f, -Pitch));

	// The head, the same way. Gaze lag and drift both arrive in here, so this
	// is what stops her facing the camera dead-on for an entire conversation.
	const float HeadRange = FMath::Max(HeadRangeDegrees, 1.0f);
	const float HeadYaw = FMath::Clamp(Face.HeadYaw / HeadRange, -1.0f, 1.0f);
	const float HeadPitch = FMath::Clamp(Face.HeadPitch / HeadRange, -1.0f, 1.0f);
	const float HeadRoll = FMath::Clamp(Face.HeadRoll / HeadRange, -1.0f, 1.0f);

	ApplyToCurves(CurveNames.TurnLeft, FMath::Max(0.0f, -HeadYaw));
	ApplyToCurves(CurveNames.TurnRight, FMath::Max(0.0f, HeadYaw));
	ApplyToCurves(CurveNames.TurnUp, FMath::Max(0.0f, HeadPitch));
	ApplyToCurves(CurveNames.TurnDown, FMath::Max(0.0f, -HeadPitch));
	ApplyToCurves(CurveNames.TiltLeft, FMath::Max(0.0f, -HeadRoll));
	ApplyToCurves(CurveNames.TiltRight, FMath::Max(0.0f, HeadRoll));
}

void UAmandaFaceAnimInstance::ApplyToCurves(const TArray<FName>& Names, float Value)
{
	for (const FName& Name : Names)
	{
		if (!Name.IsNone())
		{
			AddCurveValue(Name, Value);
		}
	}
}

TArray<FName> UAmandaFaceCurveLibrary::ListCurveNames(USkeletalMesh* Mesh, const FString& Contains)
{
	TArray<FName> Names;
	if (Mesh == nullptr)
	{
		return Names;
	}

	USkeleton* Skeleton = Mesh->GetSkeleton();
	if (Skeleton == nullptr)
	{
		return Names;
	}

	Skeleton->GetCurveMetaDataNames(Names);

	if (!Contains.IsEmpty())
	{
		Names.RemoveAll([&Contains](const FName& Name)
		{
			return !Name.ToString().Contains(Contains, ESearchCase::IgnoreCase);
		});
	}

	Names.Sort(FNameLexicalLess());
	return Names;
}
