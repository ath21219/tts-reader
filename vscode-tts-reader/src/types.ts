/** Bridge Server ← Client メッセージ */
export interface BridgeRequest {
  method: "speak" | "stop" | "configure";
  params?: Record<string, unknown>;
}

/** Bridge Server → Client: 再生イベント */
export interface PlaybackChunk {
  index: number;
  content: string;
  type: "text" | "pause" | "skip";
  source_offset: number;
  source_length: number;
  pause_duration: number;
}

export interface PlaybackEventData {
  state: "playing" | "idle" | "paused" | "stopped";
  chunk: PlaybackChunk;
}

export interface BridgeEvent {
  event: "playback" | "done" | "error" | "configured";
  data?: PlaybackEventData | { message: string } | Record<string, unknown>;
}

export type PlaybackCallback = (data: PlaybackEventData) => void;
export type DoneCallback = () => void;
export type ErrorCallback = (message: string) => void;
