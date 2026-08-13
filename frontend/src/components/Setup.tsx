import { useCallback, useEffect, useRef, useState } from "react";
import { useBootstrapProject } from "../api/hooks";
import { ApiError, readTextFile } from "../api/client";
import { useCoords } from "../store";
import type { BootstrapResult } from "../api/types";
import { BlankBookForm } from "./onboarding/BlankBookForm";
import { ImportReceipt } from "./onboarding/ImportReceipt";
import { ImportReview } from "./onboarding/ImportReview";
import { OnboardingShell } from "./onboarding/OnboardingShell";
import { StartChooser } from "./onboarding/StartChooser";

type Mode = "choose" | "import-review" | "import-done" | "blank-name";

export function Setup({ onClose }: { onClose?: () => void }) {
  const [mode, setMode] = useState<Mode>("choose");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [reading, setReading] = useState(false);
  const [readingError, setReadingError] = useState<string | null>(null);
  /** 刚导完那一份回执。留着是因为「进入工作台」那颗按钮要用它开书。 */
  const [done, setDone] = useState<BootstrapResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const readTokenRef = useRef(0);
  const bootstrap = useBootstrapProject();
  const { setProject, setChapter } = useCoords();
  const mutationError = bootstrap.error instanceof ApiError ? bootstrap.error.message : null;

  const closeDrawer = useCallback(() => {
    if (bootstrap.isPending) return;
    readTokenRef.current += 1;
    // 回执摆着的时候，「关掉」和「进入工作台」是同一件事：书已经建好了，
    // 从这儿关掉却不开书，等于让他自己回书架上再找一遍刚导进来的那本。
    if (done) {
      setProject(done.project.id);
      setChapter(done.initial_chapter);
    }
    onClose?.();
  }, [bootstrap.isPending, done, onClose, setChapter, setProject]);

  useEffect(() => {
    if (!onClose) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeDrawer();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [closeDrawer, onClose]);

  function back() {
    readTokenRef.current += 1;
    bootstrap.reset();
    setReadingError(null);
    setReading(false);
    setMode("choose");
  }

  async function selectFile(nextFile: File) {
    const token = ++readTokenRef.current;
    bootstrap.reset();
    setFile(nextFile);
    setText("");
    setName(nextFile.name.replace(/\.txt$/i, ""));
    setReadingError(null);
    setMode("import-review");
    setReading(true);
    try {
      const nextText = await readTextFile(nextFile);
      if (readTokenRef.current === token) setText(nextText);
    } catch {
      if (readTokenRef.current === token) {
        setReadingError("读取 TXT 失败，请重新选择文件。");
      }
    } finally {
      if (readTokenRef.current === token) setReading(false);
    }
  }

  function changeName(nextName: string) {
    setName(nextName);
    bootstrap.reset();
  }

  function finish(result: { project: { id: string }; initial_chapter: number }) {
    setProject(result.project.id);
    setChapter(result.initial_chapter);
    onClose?.();
  }

  function submitImport() {
    const trimmed = name.trim();
    if (!file || !text || !trimmed) return;
    bootstrap.mutate(
      { mode: "import", name: trimmed, text },
      {
        // **导完不直接进工作台**：整份回执此前被丢在地上，而里面唯一那个全书性的
        // 信号（`preamble_chars` 异常 ⇒ 全书章号可能错一位）只有在他往里记第一条
        // 事实**之前**说出来才有用——进了工作台再说就晚了。
        // 后端答不出回执的话（空白档、或者哪天出参变了）就照旧直接进去，
        // 而不是卡在一块空屏幕上。
        onSuccess: (result) => {
          setDone(result);
          if (result.summary) setMode("import-done");
          else finish(result);
        },
      },
    );
  }

  function submitBlank() {
    const trimmed = name.trim();
    if (!trimmed) return;
    bootstrap.mutate({ mode: "blank", name: trimmed }, { onSuccess: finish });
  }

  const content = (() => {
    if (mode === "import-done" && done?.summary) {
      return <ImportReceipt summary={done.summary} onEnter={() => finish(done)} />;
    }
    if (mode === "import-review") {
      return (
        <ImportReview
          file={file}
          hasText={!!text}
          name={name}
          reading={reading}
          pending={bootstrap.isPending}
          error={readingError ?? mutationError}
          onNameChange={changeName}
          onBack={back}
          onSubmit={submitImport}
        />
      );
    }
    if (mode === "blank-name") {
      return (
        <BlankBookForm
          name={name}
          pending={bootstrap.isPending}
          error={mutationError}
          onNameChange={changeName}
          onBack={back}
          onSubmit={submitBlank}
        />
      );
    }
    return (
      <StartChooser
        fileInputRef={fileInputRef}
        onFile={selectFile}
        onBlank={() => {
          readTokenRef.current += 1;
          bootstrap.reset();
          setReadingError(null);
          setReading(false);
          setName("");
          setMode("blank-name");
        }}
      />
    );
  })();

  const shell = <OnboardingShell compact={!!onClose}>{content}</OnboardingShell>;
  if (!onClose) return <div className="setup-full">{shell}</div>;
  return (
    <>
      <div className="backdrop" onClick={closeDrawer} />
      <div className="drawer onboarding-drawer" role="dialog" aria-modal="true" aria-label="新建 / 导入小说">
        <button
          className="onboarding-close"
          type="button"
          disabled={bootstrap.isPending}
          onClick={closeDrawer}
        >
          关闭
        </button>
        {shell}
      </div>
    </>
  );
}
