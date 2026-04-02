import * as vscode from "vscode";
import { PlaybackEventData } from "./types";

/**
 * エディタ上で読み上げ中のテキスト範囲をハイライトする
 */
export class Highlighter {
  private decoration: vscode.TextEditorDecorationType;
  // 再生完了後の薄いハイライト
  private dimDecoration: vscode.TextEditorDecorationType;
  private activeRanges: vscode.Range[] = [];

  constructor() {
    this.decoration = vscode.window.createTextEditorDecorationType({
      backgroundColor: new vscode.ThemeColor(
        "editor.findMatchHighlightBackground"
      ),
      borderRadius: "2px",
      isWholeLine: false,
    });
    // 再生完了した chunk を薄くハイライト
    this.dimDecoration = vscode.window.createTextEditorDecorationType({
      backgroundColor: "rgba(128, 128, 128, 0.1)",
      borderRadius: "2px",
      isWholeLine: false,
    });
  }

  /**
   * 再生イベントに基づいてハイライトを更新する
   */
  update(editor: vscode.TextEditor, data: PlaybackEventData): void {
    if (data.state === "playing" && data.chunk.type === "text") {
      const startPos = editor.document.positionAt(data.chunk.source_offset);
      const endPos = editor.document.positionAt(
        data.chunk.source_offset + data.chunk.source_length
      );
      const range = new vscode.Range(startPos, endPos);

      // 直前のアクティブハイライトを薄い色に退避
      if (this.activeRanges.length > 0) {
        editor.setDecorations(this.dimDecoration, this.activeRanges);
      }

      this.activeRanges = [range];
      editor.setDecorations(this.decoration, this.activeRanges);

      // ハイライト位置が見えるようスクロール
      editor.revealRange(
        range,
        vscode.TextEditorRevealType.InCenterIfOutsideViewport
      );
    } else if (data.state === "idle" && data.chunk.type === "text") {
      // chunk 再生完了 → 薄いハイライトに切り替え
      // 次の text chunk の playing イベントで上書きされる
      if (this.activeRanges.length > 0) {
        editor.setDecorations(this.dimDecoration, this.activeRanges);
        editor.setDecorations(this.decoration, []);
      }
    }
    // pause / skip の idle は無視（ハイライト位置は変えない）
  }

  /**
   * ハイライトをすべてクリアする
   */
  clear(editor: vscode.TextEditor): void {
    this.activeRanges = [];
    editor.setDecorations(this.decoration, []);
    editor.setDecorations(this.dimDecoration, []);
  }

  dispose(): void {
    this.decoration.dispose();
    this.dimDecoration.dispose();
  }
}
