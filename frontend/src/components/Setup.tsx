import { useRef, useState } from "react";
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
  const bootstrap = useBootstrapProject();
  const { setProject, setChapter } = useCoords();
  const mutationError = bootstrap.error instanceof ApiError ? bootstrap.error.message : null;

  function back() {
    bootstrap.reset();
    setReadingError(null);
    setMode("choose");
  }

  async function selectFile(nextFile: File) {
    bootstrap.reset();
    setFile(nextFile);
    setText("");
    setName(nextFile.name.replace(/\.txt$/i, ""));
    setReadingError(null);
    setMode("import-review");
    setReading(true);
    try {
      setText(await readTextFile(nextFile));
    } catch {
      setReadingError("读取 TXT 失败，请重新选择文件。");
    } finally {
      setReading(false);
    }
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
          onNameChange={setName}
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
          onNameChange={setName}
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
          bootstrap.reset();
          setReadingError(null);
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
      <div className="backdrop" onClick={onClose} />
      <div className="drawer">{shell}</div>
    </>
  );
}
