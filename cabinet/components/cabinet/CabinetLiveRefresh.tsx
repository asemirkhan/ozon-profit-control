'use client';

import { startTransition, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';

type PackMeta = {
  generatedAt: string | null;
  lastModifiedAt: string;
  etag: string;
};

function resolveVersion(meta: PackMeta) {
  return meta.generatedAt || meta.etag || meta.lastModifiedAt;
}

export function CabinetLiveRefresh() {
  const router = useRouter();
  const seenVersionRef = useRef<string | null>(null);

  useEffect(() => {
    let stopped = false;

    async function pollMeta() {
      try {
        const response = await fetch('/api/profit-control-pack-meta', {
          cache: 'no-store',
          headers: {
            'cache-control': 'no-store, no-cache, max-age=0',
          },
        });
        if (!response.ok || stopped) return;
        const meta = (await response.json()) as PackMeta;
        const version = resolveVersion(meta);
        if (!seenVersionRef.current) {
          seenVersionRef.current = version;
          return;
        }
        if (version && version !== seenVersionRef.current) {
          seenVersionRef.current = version;
          startTransition(() => {
            router.refresh();
          });
        }
      } catch {
        // Keep the cabinet usable even if the probe temporarily fails.
      }
    }

    const intervalId = window.setInterval(pollMeta, 10000);
    const focusHandler = () => {
      void pollMeta();
    };

    void pollMeta();
    window.addEventListener('focus', focusHandler);
    document.addEventListener('visibilitychange', focusHandler);

    return () => {
      stopped = true;
      window.clearInterval(intervalId);
      window.removeEventListener('focus', focusHandler);
      document.removeEventListener('visibilitychange', focusHandler);
    };
  }, [router]);

  return null;
}
