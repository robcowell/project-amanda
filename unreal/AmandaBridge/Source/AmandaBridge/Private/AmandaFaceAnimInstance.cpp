// Project Amanda -- presence onto the rig, without touching the graph.

#include "AmandaFaceAnimInstance.h"
#include "AmandaPresence.h"

#include "Animation/AnimationPoseData.h"
#include "Animation/Skeleton.h"
#include "AnimationRuntime.h"
#include "BonePose.h"
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

	if (!bApplyPresence || !bApplyCurves)
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

	// Kept for rigs where these curves are live. On this MetaHuman they are
	// not: saturating them moves the head by 0.6 of a pixel, because the head
	// is posed by the graph rather than by the face board at runtime.
	ApplyToCurves(CurveNames.TurnLeft, FMath::Max(0.0f, -HeadYaw));
	ApplyToCurves(CurveNames.TurnRight, FMath::Max(0.0f, HeadYaw));
	ApplyToCurves(CurveNames.TurnUp, FMath::Max(0.0f, HeadPitch));
	ApplyToCurves(CurveNames.TurnDown, FMath::Max(0.0f, -HeadPitch));
	ApplyToCurves(CurveNames.TiltLeft, FMath::Max(0.0f, -HeadRoll));
	ApplyToCurves(CurveNames.TiltRight, FMath::Max(0.0f, HeadRoll));

	// The head is posed by the proxy, after the graph, from PresenceHeadRotation.
}

FAnimInstanceProxy* UAmandaFaceAnimInstance::CreateAnimInstanceProxy()
{
	return new FAmandaFaceAnimProxy(this);
}

void FAmandaFaceAnimProxy::PreUpdate(UAnimInstance* InAnimInstance, float DeltaSeconds)
{
	FAnimInstanceProxy::PreUpdate(InAnimInstance, DeltaSeconds);

	// Evaluation runs on a worker thread and must not touch the anim instance,
	// so everything it needs is copied here, on the game thread.
	if (const UAmandaFaceAnimInstance* Face = Cast<UAmandaFaceAnimInstance>(InAnimInstance))
	{
		HeadRotation = Face->PresenceHeadRotation;
		HeadBone = Face->HeadBone;
		NeckBone = Face->NeckBone;
		NeckShare = FMath::Clamp(Face->NeckShare, 0.0f, 1.0f);
		bApply = Face->bApplyPresence && Face->bApplyHeadRotation && Face->bHasPresence;
	}
}

namespace
{
	/**
	 * Turn one bone in component space, leaving its children to follow.
	 *
	 * Component space rather than bone space because the head's own axes are
	 * not the ones a director thinks in -- its rest orientation on this rig
	 * measures 90 degrees of pitch, so "yaw" applied locally would tilt her
	 * head sideways. In component space, with the character facing forward,
	 * yaw/pitch/roll mean what they say.
	 *
	 * Only the bones read here are marked as component space, so converting
	 * back leaves every child's local transform untouched and the whole face
	 * rides along -- which is what a head turn is.
	 */
	/** True only if the bone was found and actually turned. */
	bool RotateBoneComponentSpace(FCSPose<FCompactPose>& CSPose, const FName& BoneName,
		const FRotator& Delta)
	{
		if (BoneName.IsNone() || Delta.IsNearlyZero())
		{
			return false;
		}

		const FBoneContainer& Bones = CSPose.GetPose().GetBoneContainer();
		const int32 MeshIndex = Bones.GetPoseBoneIndexForBoneName(BoneName);
		if (MeshIndex == INDEX_NONE)
		{
			return false;
		}

		const FCompactPoseBoneIndex Index = Bones.MakeCompactPoseIndex(FMeshPoseBoneIndex(MeshIndex));
		if (Index.GetInt() == INDEX_NONE)
		{
			return false;
		}

		FTransform Transform = CSPose.GetComponentSpaceTransform(Index);
		Transform.SetRotation(Delta.Quaternion() * Transform.GetRotation());
		CSPose.SetComponentSpaceTransform(Index, Transform);
		return true;
	}
}

bool FAmandaFaceAnimProxy::Evaluate(FPoseContext& Output)
{
	const bool bResult = FAnimInstanceProxy::Evaluate(Output);

	bPosedHead = false;
	if (!bApply || HeadRotation.IsNearlyZero())
	{
		return bResult;
	}

	const FRotator NeckPart = HeadRotation * NeckShare;
	const FRotator HeadPart = HeadRotation * (1.0f - NeckShare);

	FCSPose<FCompactPose> CSPose;
	CSPose.InitPose(Output.Pose);
	RotateBoneComponentSpace(CSPose, NeckBone, NeckPart);
	// The head is the one that has to land. Reporting success because the code
	// ran, rather than because a bone moved, is how a missing bone name reads
	// as "something downstream is overwriting me" -- which cost an hour once.
	bPosedHead = RotateBoneComponentSpace(CSPose, HeadBone, HeadPart);
	FCSPose<FCompactPose>::ConvertComponentPosesToLocalPoses(MoveTemp(CSPose), Output.Pose);

	return bResult;
}

void FAmandaFaceAnimProxy::PostUpdate(UAnimInstance* InAnimInstance) const
{
	FAnimInstanceProxy::PostUpdate(InAnimInstance);

	if (UAmandaFaceAnimInstance* Face = Cast<UAmandaFaceAnimInstance>(InAnimInstance))
	{
		Face->bDrivingHead = bPosedHead;
	}
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
