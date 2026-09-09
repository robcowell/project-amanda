// Project Amanda -- the WebSocket client that drives the avatar.

#include "AmandaBridgeSubsystem.h"

#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "IWebSocket.h"
#include "Modules/ModuleManager.h"
#include "WebSocketsModule.h"

DEFINE_LOG_CATEGORY_STATIC(LogAmandaBridge, Log, All);

void UAmandaBridgeSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
	Super::Initialize(Collection);

	if (!FModuleManager::Get().IsModuleLoaded(TEXT("WebSockets")))
	{
		FModuleManager::Get().LoadModule(TEXT("WebSockets"));
	}

	if (bAutoConnect)
	{
		Connect(Host, Port);
	}
}

void UAmandaBridgeSubsystem::Deinitialize()
{
	bWantConnection = false;
	CancelReconnect();
	CloseSocket();
	Super::Deinitialize();
}

// --------------------------------------------------------------------------
// Control
// --------------------------------------------------------------------------

void UAmandaBridgeSubsystem::Connect(const FString& InHost, int32 InPort)
{
	Host = InHost;
	Port = InPort;
	bWantConnection = true;
	BackoffIndex = 0;

	CancelReconnect();
	CloseSocket();
	OpenSocket();
}

void UAmandaBridgeSubsystem::Disconnect()
{
	bWantConnection = false;
	CancelReconnect();
	CloseSocket();
}

bool UAmandaBridgeSubsystem::IsConnected() const
{
	return Socket.IsValid() && Socket->IsConnected();
}

// --------------------------------------------------------------------------
// Socket lifecycle
// --------------------------------------------------------------------------

void UAmandaBridgeSubsystem::OpenSocket()
{
	const FString Url = FString::Printf(TEXT("ws://%s:%d"), *Host, Port);
	UE_LOG(LogAmandaBridge, Log, TEXT("connecting to %s"), *Url);

	Socket = FWebSocketsModule::Get().CreateWebSocket(Url, TEXT(""));
	if (!Socket.IsValid())
	{
		LastError = TEXT("could not create a WebSocket");
		UE_LOG(LogAmandaBridge, Error, TEXT("%s"), *LastError);
		ScheduleReconnect();
		return;
	}

	Socket->OnConnected().AddUObject(this, &UAmandaBridgeSubsystem::HandleConnected);
	Socket->OnConnectionError().AddUObject(this, &UAmandaBridgeSubsystem::HandleConnectionError);
	Socket->OnClosed().AddUObject(this, &UAmandaBridgeSubsystem::HandleClosed);
	Socket->OnMessage().AddUObject(this, &UAmandaBridgeSubsystem::HandleMessage);
	Socket->Connect();
}

void UAmandaBridgeSubsystem::CloseSocket()
{
	if (!Socket.IsValid())
	{
		return;
	}

	// Unbind before closing: a queued callback firing into a half-torn-down
	// subsystem is the classic way this crashes on level change.
	Socket->OnConnected().RemoveAll(this);
	Socket->OnConnectionError().RemoveAll(this);
	Socket->OnClosed().RemoveAll(this);
	Socket->OnMessage().RemoveAll(this);

	if (Socket->IsConnected())
	{
		Socket->Close();
	}
	Socket.Reset();
}

void UAmandaBridgeSubsystem::ScheduleReconnect()
{
	if (!bWantConnection || ReconnectBackoffMs.Num() == 0)
	{
		return;
	}
	CancelReconnect();

	const int32 Index = FMath::Min(BackoffIndex, ReconnectBackoffMs.Num() - 1);
	const float Delay = ReconnectBackoffMs[Index] / 1000.f;
	BackoffIndex++;

	UE_LOG(LogAmandaBridge, Verbose, TEXT("retrying in %.2fs"), Delay);

	ReconnectHandle = FTSTicker::GetCoreTicker().AddTicker(
		FTickerDelegate::CreateLambda([this](float) -> bool
		{
			ReconnectHandle.Reset();
			if (bWantConnection)
			{
				CloseSocket();
				OpenSocket();
			}
			return false; // one-shot
		}),
		Delay);
}

void UAmandaBridgeSubsystem::CancelReconnect()
{
	if (ReconnectHandle.IsValid())
	{
		FTSTicker::GetCoreTicker().RemoveTicker(ReconnectHandle);
		ReconnectHandle.Reset();
	}
}

// --------------------------------------------------------------------------
// Socket callbacks
// --------------------------------------------------------------------------

void UAmandaBridgeSubsystem::HandleConnected()
{
	BackoffIndex = 0;
	LastError.Empty();
	UE_LOG(LogAmandaBridge, Log, TEXT("bridge connected"));
	OnBridgeConnected.Broadcast();
}

void UAmandaBridgeSubsystem::HandleConnectionError(const FString& Error)
{
	LastError = Error;
	// Expected while the orchestrator is not running yet, so this is not an
	// error-level log -- it would drown the output during ordinary startup.
	UE_LOG(LogAmandaBridge, Verbose, TEXT("connection failed: %s"), *Error);
	OnBridgeDisconnected.Broadcast(Error);
	ScheduleReconnect();
}

void UAmandaBridgeSubsystem::HandleClosed(int32 StatusCode, const FString& Reason, bool bWasClean)
{
	UE_LOG(LogAmandaBridge, Log, TEXT("bridge closed (%d, clean=%d): %s"),
		StatusCode, bWasClean ? 1 : 0, *Reason);
	OnBridgeDisconnected.Broadcast(Reason);
	ScheduleReconnect();
}

void UAmandaBridgeSubsystem::HandleMessage(const FString& Message)
{
	// The engine's WebSockets module delivers callbacks on the game thread, so
	// this is normally a straight call. The hop is here because these delegates
	// are Blueprint-assignable, and broadcasting one off the game thread would
	// fail in ways that are extremely hard to attribute back to here.
	if (IsInGameThread())
	{
		DispatchMessage(Message);
		return;
	}

	TWeakObjectPtr<UAmandaBridgeSubsystem> WeakThis(this);
	AsyncTask(ENamedThreads::GameThread, [WeakThis, Message]()
	{
		if (UAmandaBridgeSubsystem* Self = WeakThis.Get())
		{
			Self->DispatchMessage(Message);
		}
	});
}

// --------------------------------------------------------------------------
// Routing
// --------------------------------------------------------------------------

void UAmandaBridgeSubsystem::DispatchMessage(const FString& Message)
{
	FAmandaEnvelope Envelope;
	FString Error;
	if (!FAmandaProtocol::Decode(Message, Envelope, Error))
	{
		MalformedMessages++;
		LastError = Error;
		UE_LOG(LogAmandaBridge, Warning, TEXT("undecodable message: %s"), *Error);
		return;
	}

	if (!FAmandaProtocol::IsKnownEvent(Envelope.Event))
	{
		// Skipping is correct: it is what lets a newer orchestrator drive an
		// older renderer without both being released together.
		UnknownEventsSkipped++;
		UE_LOG(LogAmandaBridge, Verbose, TEXT("skipping unknown event '%s'"), *Envelope.Event);
		OnUnknownEvent.Broadcast(Envelope.Event);
		return;
	}

	EventsReceived++;
	const FString& Event = Envelope.Event;

	if (Event == AmandaEvents::PerformanceUpdate)
	{
		FAmandaPerformanceUpdate Update;
		if (FAmandaProtocol::ParsePerformance(Envelope, Update, Error))
		{
			Update.Preset = FAmandaProtocol::Animatable(Update.Preset);
			OnPerformanceUpdate.Broadcast(Update);
		}
	}
	else if (Event == AmandaEvents::GazeSetTarget)
	{
		FAmandaGaze Gaze;
		if (FAmandaProtocol::ParseGaze(Envelope, Gaze, Error))
		{
			OnGazeSetTarget.Broadcast(Gaze);
		}
	}
	else if (Event == AmandaEvents::SpeechPrepare
		|| Event == AmandaEvents::SpeechStarted
		|| Event == AmandaEvents::SpeechCompleted
		|| Event == AmandaEvents::SpeechCancelled)
	{
		const EAmandaSpeechPhase Phase =
			Event == AmandaEvents::SpeechPrepare   ? EAmandaSpeechPhase::Prepare :
			Event == AmandaEvents::SpeechStarted   ? EAmandaSpeechPhase::Started :
			Event == AmandaEvents::SpeechCompleted ? EAmandaSpeechPhase::Completed :
			                                         EAmandaSpeechPhase::Cancelled;

		FAmandaSpeech Speech;
		if (FAmandaProtocol::ParseSpeech(Envelope, Phase, Speech, Error))
		{
			OnSpeech.Broadcast(Phase, Speech);
		}
	}
	else if (Event == AmandaEvents::AvatarReset)
	{
		OnAvatarReset.Broadcast();
	}
	else if (Event == AmandaEvents::GestureTrigger)
	{
		FAmandaGesture Gesture;
		if (FAmandaProtocol::ParseGesture(Envelope, Gesture, Error))
		{
			OnGestureTrigger.Broadcast(Gesture);
		}
	}
	else if (Event == AmandaEvents::UserDetected)
	{
		bool bPresent = false;
		if (FAmandaProtocol::ParseUserPresence(Envelope, bPresent, Error))
		{
			OnUserPresence.Broadcast(bPresent);
		}
	}
	else if (Event == AmandaEvents::UserSpeechStarted)
	{
		OnUserSpeech.Broadcast(true, 0);
	}
	else if (Event == AmandaEvents::UserSpeechEnded)
	{
		int32 DurationMs = 0;
		FAmandaProtocol::ParseUserSpeechEnded(Envelope, DurationMs);
		OnUserSpeech.Broadcast(false, DurationMs);
	}
	else if (Event == AmandaEvents::ThinkingStarted)
	{
		OnThinking.Broadcast(true);
	}
	else if (Event == AmandaEvents::ThinkingEnded)
	{
		OnThinking.Broadcast(false);
	}
	else if (Event == AmandaEvents::SessionStarted || Event == AmandaEvents::SessionEnded)
	{
		FAmandaSession Session;
		if (FAmandaProtocol::ParseSession(Envelope, Session, Error))
		{
			if (Event == AmandaEvents::SessionStarted)
			{
				OnSessionStarted.Broadcast(Session);
			}
			else
			{
				OnSessionEnded.Broadcast(Session);
			}
		}
	}

	if (!Error.IsEmpty())
	{
		MalformedMessages++;
		LastError = Error;
		UE_LOG(LogAmandaBridge, Warning, TEXT("bad payload for '%s': %s"), *Event, *Error);
	}
}
