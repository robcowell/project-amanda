// Project Amanda -- the WebSocket client that drives the avatar.
//
// A GameInstance subsystem rather than an Actor, so it survives level changes
// and there is exactly one of it. Get it in Blueprints with
// "Get Game Instance Subsystem" -> Amanda Bridge Subsystem, then bind the
// events below in BeginPlay.

#pragma once

#include "AmandaBridgeProtocol.h"
#include "AmandaBridgeTypes.h"
#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "Subsystems/GameInstanceSubsystem.h"
#include "AmandaBridgeSubsystem.generated.h"

class IWebSocket;

DECLARE_DYNAMIC_MULTICAST_DELEGATE(FAmandaSimpleSignature);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAmandaTextSignature, FString, Text);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAmandaSessionSignature, FAmandaSession, Session);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAmandaFlagSignature, bool, bValue);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FAmandaUserSpeechSignature, bool, bSpeaking, int32, DurationMs);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FAmandaSpeechSignature, EAmandaSpeechPhase, Phase, FAmandaSpeech, Speech);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAmandaPerformanceSignature, FAmandaPerformanceUpdate, Update);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAmandaGazeSignature, FAmandaGaze, Gaze);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FAmandaGestureSignature, FAmandaGesture, Gesture);

/**
 * Receives protocol v1 and broadcasts it to Blueprints.
 *
 * The orchestrator is the server and this is the client, which is the right way
 * round: the conversation outlives the renderer, so Unreal can be restarted or
 * attached to a debugger without taking Claude and the conversation state down
 * with it. Reconnecting is therefore normal, not exceptional -- and on every
 * connection the orchestrator sends avatar.reset followed by the current
 * session, performance and gaze state, so this side never has to remember
 * anything across a drop.
 */
UCLASS(DisplayName = "Amanda Bridge Subsystem")
class AMANDABRIDGE_API UAmandaBridgeSubsystem : public UGameInstanceSubsystem
{
	GENERATED_BODY()

public:
	// -- UGameInstanceSubsystem ------------------------------------------

	virtual void Initialize(FSubsystemCollectionBase& Collection) override;
	virtual void Deinitialize() override;

	// -- Control ---------------------------------------------------------

	/** Connect, or reconnect elsewhere. Safe to call while already connected. */
	UFUNCTION(BlueprintCallable, Category = "Amanda|Bridge")
	void Connect(const FString& InHost, int32 InPort);

	/** Close and stop retrying. Reconnection resumes only on another Connect. */
	UFUNCTION(BlueprintCallable, Category = "Amanda|Bridge")
	void Disconnect();

	UFUNCTION(BlueprintPure, Category = "Amanda|Bridge")
	bool IsConnected() const;

	// -- Configuration ---------------------------------------------------

	/** Loopback by default. Do not point this at a network address casually. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Config, Category = "Amanda|Bridge")
	FString Host = TEXT("127.0.0.1");

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Config, Category = "Amanda|Bridge")
	int32 Port = 8765;

	/** Connect as soon as the game instance starts. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Config, Category = "Amanda|Bridge")
	bool bAutoConnect = true;

	/**
	 * Retry delays, in milliseconds, matching config/avatar.yaml. The last
	 * entry repeats forever -- the renderer should keep trying indefinitely,
	 * because the orchestrator restarting is an ordinary event.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Config, Category = "Amanda|Bridge")
	TArray<int32> ReconnectBackoffMs = { 250, 500, 1000, 2000, 5000 };

	// -- Diagnostics -----------------------------------------------------

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Bridge")
	int32 EventsReceived = 0;

	/** Messages that decoded but named an event this build does not implement. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Bridge")
	int32 UnknownEventsSkipped = 0;

	/** Messages that failed to decode at all. Non-zero means a real problem. */
	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Bridge")
	int32 MalformedMessages = 0;

	UPROPERTY(BlueprintReadOnly, Category = "Amanda|Bridge")
	FString LastError;

	// -- Events ----------------------------------------------------------

	UPROPERTY(BlueprintAssignable, Category = "Amanda|Bridge")
	FAmandaSimpleSignature OnBridgeConnected;

	UPROPERTY(BlueprintAssignable, Category = "Amanda|Bridge")
	FAmandaTextSignature OnBridgeDisconnected;

	UPROPERTY(BlueprintAssignable, Category = "Amanda|Session")
	FAmandaSessionSignature OnSessionStarted;

	UPROPERTY(BlueprintAssignable, Category = "Amanda|Session")
	FAmandaSessionSignature OnSessionEnded;

	/** False means the user has walked away, not that they were never there. */
	UPROPERTY(BlueprintAssignable, Category = "Amanda|User")
	FAmandaFlagSignature OnUserPresence;

	/** DurationMs is only meaningful when bSpeaking is false. */
	UPROPERTY(BlueprintAssignable, Category = "Amanda|User")
	FAmandaUserSpeechSignature OnUserSpeech;

	/** Reduce eye contact and still the head. Do not look theatrically puzzled. */
	UPROPERTY(BlueprintAssignable, Category = "Amanda|Assistant")
	FAmandaFlagSignature OnThinking;

	/** All four phases of an utterance. Switch on Phase. */
	UPROPERTY(BlueprintAssignable, Category = "Amanda|Speech")
	FAmandaSpeechSignature OnSpeech;

	UPROPERTY(BlueprintAssignable, Category = "Amanda|Performance")
	FAmandaPerformanceSignature OnPerformanceUpdate;

	UPROPERTY(BlueprintAssignable, Category = "Amanda|Performance")
	FAmandaGazeSignature OnGazeSetTarget;

	UPROPERTY(BlueprintAssignable, Category = "Amanda|Performance")
	FAmandaGestureSignature OnGestureTrigger;

	/** Drop all performance state and settle to neutral attentive. */
	UPROPERTY(BlueprintAssignable, Category = "Amanda|Performance")
	FAmandaSimpleSignature OnAvatarReset;

	/**
	 * An event this build does not implement. Bound only for diagnosing version
	 * skew -- ignoring these is correct behaviour, not a failure.
	 */
	UPROPERTY(BlueprintAssignable, Category = "Amanda|Bridge")
	FAmandaTextSignature OnUnknownEvent;

private:
	void OpenSocket();
	void CloseSocket();
	void ScheduleReconnect();
	void CancelReconnect();

	void HandleConnected();
	void HandleConnectionError(const FString& Error);
	void HandleClosed(int32 StatusCode, const FString& Reason, bool bWasClean);
	void HandleMessage(const FString& Message);

	/** Called on the game thread with a fully received text frame. */
	void DispatchMessage(const FString& Message);

	TSharedPtr<IWebSocket> Socket;
	FTSTicker::FDelegateHandle ReconnectHandle;
	int32 BackoffIndex = 0;
	bool bWantConnection = false;
};
