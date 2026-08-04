import { useCallback, useEffect, useRef, useState } from "react";
import { useBootstrapProject } from "../api/hooks";
import { ApiError, readTextFile } from "../api/client";
import { useCoords } from "../store";
import { BlankBookForm } from "./onboarding/BlankBookForm";
import { ImportReview } from "./onboarding/ImportReview";
import { OnboardingShell } from "./onboarding/OnboardingShell";
import { StartChooser } from "./onboarding/StartChooser";

type Mode = "choose" | "import-review" | "blank-name";

export function Setup({ onClose }: { onClose?: () => void }) {
  const [mode, setMode] = useState<Mode>("choose");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [reading, setReading] = useState(false);
  const [readingError, setReadingError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const readTokenRef = useRef(0);
  const bootstrap = useBootstrapProject();
  const { setProject, setChapter } = useCoords();
  const mutationError = bootstrap.error instanceof ApiError ? bootstrap.error.message : null;

  const closeDrawer = useCallback(() => {
    if (bootstrap.isPending) return;
    readTokenRef.current += 1;
    onClose?.();
  }, [bootstrap.isPending, onClose]);

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
    bootstrap.mutate({ mode: "import", name: trimmed, text }, { onSuccess: finish });
  }

  function submitBlank() {
    const trimmed = name.trim();
    if (!trimmed) return;
    bootstrap.mutate({ mode: "blank", name: trimmed }, { onSuccess: finish });
  }

  const content = (() => {
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
      <div className="drawer" role="dialog" aria-modal="true" aria-label="新建 / 导入小说">
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
