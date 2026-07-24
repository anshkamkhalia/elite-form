import { useEffect, useState } from "react";
import { videoUrlFromKey } from "../config";
import { IconInfo } from "./icons";

// Plays a stored video. Prefers the backend's presigned R2 URL (videoUrl),
// which works even on private buckets; falls back to joining the public base
// URL with the object key. If neither loads, explains why instead of showing
// a broken player.
export default function VideoResult({ videoKey, videoUrl, label }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const url = videoUrl || videoUrlFromKey(videoKey);

  useEffect(() => {
    setFailed(false);
  }, [url]);

  return (
    <div>
      {url && !failed ? (
        <video
          className="result-video"
          src={url}
          controls
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="alert alert-info">
          <IconInfo size={16} />
          <div>
            {failed ? (
              <>
                {label} is stored in Cloudflare R2 but couldn&apos;t be
                streamed. The configured base URL is R2&apos;s private S3
                endpoint, which rejects unsigned browser requests — enable
                public access on the bucket (a <code>pub-*.r2.dev</code> URL or
                custom domain) or run the Flask backend, which returns a
                presigned playback URL.
              </>
            ) : (
              <>
                {label} was uploaded to Cloudflare R2 storage. To play it here,
                set <code>VITE_R2_PUBLIC_BASE_URL</code> in{" "}
                <code>frontend/.env</code> and restart the dev server.
              </>
            )}
          </div>
        </div>
      )}
      <div className="key-row">
        <span className="k">key</span>
        <span className="key-val" title={videoKey}>
          {videoKey}
        </span>
        <button
          type="button"
          onClick={() => {
            navigator.clipboard.writeText(videoKey).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}
