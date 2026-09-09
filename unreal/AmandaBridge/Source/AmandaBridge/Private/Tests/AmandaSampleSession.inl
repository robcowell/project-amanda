// Generated -- do not edit.
//
// The sample session, emitted by the Python reference implementation, so the
// C++ decoder is tested against the exact bytes the orchestrator sends rather
// than against a hand-typed approximation of them.
//
// Regenerate from avatar-orchestrator/:
//     python3 tools/regenerate_unreal_fixture.py

static const TCHAR* AmandaSampleSession[] = {
	TEXT("{\"version\":1,\"event\":\"session.started\",\"timestamp\":1788967200.0,\"payload\":{\"session_id\":\"s_demo\"}}"),
	TEXT("{\"version\":1,\"event\":\"avatar.reset\",\"timestamp\":1788967200.01,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"performance.update\",\"timestamp\":1788967200.02,\"payload\":{\"preset\":\"neutral_attentive\",\"intensity\":0.1,\"transition_ms\":450}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967200.03,\"payload\":{\"target\":\"distant\",\"hold_ms\":0,\"transition_ms\":220}}"),
	TEXT("{\"version\":1,\"event\":\"user.detected\",\"timestamp\":1788967203.0,\"payload\":{\"present\":true}}"),
	TEXT("{\"version\":1,\"event\":\"performance.update\",\"timestamp\":1788967203.05,\"payload\":{\"preset\":\"listening\",\"intensity\":0.18,\"transition_ms\":450,\"eye_contact\":0.8}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967203.1,\"payload\":{\"target\":\"user\",\"hold_ms\":2600,\"transition_ms\":220}}"),
	TEXT("{\"version\":1,\"event\":\"user.speech_started\",\"timestamp\":1788967203.4,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967205.1,\"payload\":{\"target\":\"slightly_left\",\"hold_ms\":700,\"transition_ms\":220}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967205.8,\"payload\":{\"target\":\"user\",\"hold_ms\":0,\"transition_ms\":220}}"),
	TEXT("{\"version\":1,\"event\":\"user.speech_ended\",\"timestamp\":1788967206.0,\"payload\":{\"duration_ms\":2600}}"),
	TEXT("{\"version\":1,\"event\":\"gesture.trigger\",\"timestamp\":1788967206.06,\"payload\":{\"gesture\":\"small_nod\",\"intensity\":0.14}}"),
	TEXT("{\"version\":1,\"event\":\"assistant.thinking_started\",\"timestamp\":1788967206.12,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"performance.update\",\"timestamp\":1788967206.15,\"payload\":{\"preset\":\"considering\",\"intensity\":0.28,\"transition_ms\":520,\"eye_contact\":0.25,\"head_motion\":0.06,\"brow_activity\":0.1}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967206.2,\"payload\":{\"target\":\"slightly_right\",\"hold_ms\":900,\"transition_ms\":340}}"),
	TEXT("{\"version\":1,\"event\":\"speech.prepare\",\"timestamp\":1788967207.15,\"payload\":{\"utterance_id\":\"u_1042\",\"preset\":\"warm\",\"text\":\"It rained most of the morning, but it's cleared up now.\"}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967207.18,\"payload\":{\"target\":\"user\",\"hold_ms\":0,\"transition_ms\":260}}"),
	TEXT("{\"version\":1,\"event\":\"assistant.thinking_ended\",\"timestamp\":1788967207.2,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"performance.update\",\"timestamp\":1788967207.22,\"payload\":{\"preset\":\"warm\",\"intensity\":0.22,\"transition_ms\":400,\"eye_contact\":0.62,\"smile\":0.08}}"),
	TEXT("{\"version\":1,\"event\":\"speech.started\",\"timestamp\":1788967207.4,\"payload\":{\"utterance_id\":\"u_1042\",\"audio_channel\":\"stream\",\"sample_rate\":24000}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967208.9,\"payload\":{\"target\":\"slightly_left\",\"hold_ms\":600,\"transition_ms\":220}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967209.5,\"payload\":{\"target\":\"user\",\"hold_ms\":0,\"transition_ms\":220}}"),
	TEXT("{\"version\":1,\"event\":\"speech.completed\",\"timestamp\":1788967210.8,\"payload\":{\"utterance_id\":\"u_1042\"}}"),
	TEXT("{\"version\":1,\"event\":\"performance.update\",\"timestamp\":1788967210.9,\"payload\":{\"preset\":\"neutral_attentive\",\"intensity\":0.12,\"transition_ms\":900}}"),
	TEXT("{\"version\":1,\"event\":\"user.speech_started\",\"timestamp\":1788967213.0,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"user.speech_ended\",\"timestamp\":1788967214.9,\"payload\":{\"duration_ms\":1900}}"),
	TEXT("{\"version\":1,\"event\":\"assistant.thinking_started\",\"timestamp\":1788967215.0,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"speech.prepare\",\"timestamp\":1788967215.9,\"payload\":{\"utterance_id\":\"u_1043\",\"preset\":\"explaining\"}}"),
	TEXT("{\"version\":1,\"event\":\"assistant.thinking_ended\",\"timestamp\":1788967215.95,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"speech.started\",\"timestamp\":1788967216.1,\"payload\":{\"utterance_id\":\"u_1043\",\"audio_channel\":\"stream\",\"sample_rate\":24000}}"),
	TEXT("{\"version\":1,\"event\":\"user.speech_started\",\"timestamp\":1788967217.6,\"payload\":{}}"),
	TEXT("{\"version\":1,\"event\":\"performance.update\",\"timestamp\":1788967217.62,\"payload\":{\"preset\":\"listening\",\"intensity\":0.2,\"transition_ms\":140}}"),
	TEXT("{\"version\":1,\"event\":\"speech.cancelled\",\"timestamp\":1788967217.64,\"payload\":{\"utterance_id\":\"u_1043\",\"reason\":\"barge_in\",\"fade_ms\":80}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967217.66,\"payload\":{\"target\":\"user\",\"hold_ms\":0,\"transition_ms\":160}}"),
	TEXT("{\"version\":1,\"event\":\"user.speech_ended\",\"timestamp\":1788967225.0,\"payload\":{\"duration_ms\":7400}}"),
	TEXT("{\"version\":1,\"event\":\"user.detected\",\"timestamp\":1788967240.0,\"payload\":{\"present\":false}}"),
	TEXT("{\"version\":1,\"event\":\"performance.update\",\"timestamp\":1788967240.1,\"payload\":{\"preset\":\"neutral_attentive\",\"intensity\":0.06,\"transition_ms\":2000}}"),
	TEXT("{\"version\":1,\"event\":\"gaze.set_target\",\"timestamp\":1788967240.2,\"payload\":{\"target\":\"distant\",\"hold_ms\":0,\"transition_ms\":1200}}"),
	TEXT("{\"version\":1,\"event\":\"session.ended\",\"timestamp\":1788967245.0,\"payload\":{\"session_id\":\"s_demo\",\"reason\":\"user absent\"}}"),
};
