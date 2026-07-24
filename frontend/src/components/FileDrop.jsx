import { useEffect, useRef, useState } from "react";
import { IconUpload, IconFilm, IconX } from "./icons";

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Compact uploader sized for the control rail: a small drop target until a
// file is chosen, then a file chip plus a short preview.
export default function FileDrop({ file, onFile, hint }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(null);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // The backend accepts anything ffmpeg/OpenCV can read, so don't hard-filter.
  const pick = (f) => {
    if (f) onFile(f);
  };

  if (file) {
    return (
      <div>
        <div className="file-chip">
          <span className="file-icon">
            <IconFilm size={16} />
          </span>
          <span className="file-name">{file.name}</span>
          <span className="file-size">{formatSize(file.size)}</span>
          <button
            type="button"
            aria-label="Remove file"
            onClick={() => onFile(null)}
          >
            <IconX size={14} />
          </button>
        </div>
        {previewUrl && (
          <video className="preview-video" src={previewUrl} controls muted />
        )}
      </div>
    );
  }

  return (
    <>
      <div
        className={`uploader-target ${dragging ? "dragging" : ""}`}
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files?.[0]);
        }}
      >
        <div className="u-icon">
          <IconUpload size={20} />
        </div>
        <div className="u-main">
          Drop video or <span>browse</span>
        </div>
        {hint && <div className="u-hint">{hint}</div>}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        hidden
        onChange={(e) => {
          pick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </>
  );
}
