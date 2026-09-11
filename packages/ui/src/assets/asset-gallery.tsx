"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { createBrowserClient } from "@supabase/ssr";
import { Card, CardContent } from "../ui/card";
import { Button, buttonVariants } from "../ui/button";
import { cn } from "../cn";
import { Download, ImageIcon, Trash2, X } from "lucide-react";
import type { Asset, AssetVariant } from "./types";

function AssetThumbnail({ variant }: { variant: AssetVariant }) {
  const isVideo = variant.mime.startsWith("video/");
  /* eslint-disable @next/next/no-img-element */
  return (
    <div className="relative w-full h-full min-h-[10rem] bg-muted overflow-hidden flex items-center justify-center">
      {isVideo ? (
        <video
          poster={variant.thumbnailUrl}
          src={variant.url}
          className="w-full h-full object-cover"
          controls
          preload="metadata"
          onClick={(e) => e.stopPropagation()}
        />
      ) : variant.url ? (
        <img
          src={variant.thumbnailUrl || variant.url}
          alt={variant.name}
          className="w-full h-full object-cover"
        />
      ) : (
        <ImageIcon className="w-8 h-8 text-muted-foreground" />
      )}
    </div>
  );
  /* eslint-enable @next/next/no-img-element */
}

function Lightbox({
  variant,
  onClose,
}: {
  variant: AssetVariant | null;
  onClose: () => void;
}) {
  if (!variant) return null;
  const isVideo = variant.mime.startsWith("video/");

  return (
    <div
      className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-white hover:text-gray-300"
        aria-label="Close"
      >
        <X className="w-8 h-8" />
      </button>
      <div
        className="max-w-5xl max-h-full w-full flex flex-col items-center"
        onClick={(e) => e.stopPropagation()}
      >
        {isVideo ? (
          <video controls className="max-h-[80vh] w-auto" src={variant.url} />
        ) : (
          /* eslint-disable @next/next/no-img-element */
          <img
            src={variant.url}
            alt={variant.name}
            className="max-h-[80vh] w-auto object-contain"
          />
          /* eslint-enable @next/next/no-img-element */
        )}
        <div className="mt-4 text-white text-center">
          <p className="font-medium">{variant.name}</p>
          <p className="text-sm text-gray-300">{variant.type}</p>
        </div>
      </div>
    </div>
  );
}

function DeleteAssetButton({
  assetId,
  eventId,
  onDeleteAsset,
}: {
  assetId: string;
  eventId: string;
  onDeleteAsset: (
    assetId: string,
    eventId: string
  ) => Promise<{ success: boolean; error?: string }>;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  async function handleDelete() {
    if (!confirm("Delete this visual asset? This cannot be undone.")) return;
    startTransition(async () => {
      const result = await onDeleteAsset(assetId, eventId);
      if (result.success) {
        router.refresh();
      } else {
        alert(result.error || "Failed to delete asset");
      }
    });
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      className="text-destructive hover:text-destructive/80"
      disabled={isPending}
      onClick={(e) => {
        e.stopPropagation();
        handleDelete();
      }}
    >
      <Trash2 className="w-4 h-4 mr-2" />
      {isPending ? "Deleting..." : "Delete"}
    </Button>
  );
}

export function AssetGallery({
  assets,
  eventId,
  canDelete = false,
  onDeleteAsset,
  // Realtime refresh on generation jobs (Admin only — the table is internal).
  realtime,
  authCookieName,
}: {
  assets: Asset[];
  eventId: string;
  canDelete?: boolean;
  /** Server action — must be provided when canDelete is true. */
  onDeleteAsset?: (
    assetId: string,
    eventId: string
  ) => Promise<{ success: boolean; error?: string }>;
  /** Enable postgres_changes subscription on this table + 10s polling fallback. */
  realtime?: { table: string; filterColumn?: string };
  /** Per-app auth cookie name so the realtime client picks up the session. */
  authCookieName?: string;
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<AssetVariant | null>(null);

  useEffect(() => {
    if (!realtime) return;
    const supabase = createBrowserClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      authCookieName ? { cookieOptions: { name: authCookieName } } : undefined
    );
    const channel = supabase
      .channel(`${realtime.table}_${eventId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: realtime.table,
          filter: `${realtime.filterColumn ?? "event_id"}=eq.${eventId}`,
        },
        () => {
          router.refresh();
        }
      )
      .subscribe();

    // Fallback polling while realtime connects or for webhook-only environments.
    const interval = setInterval(() => {
      router.refresh();
    }, 10000);
    const stop = setTimeout(() => clearInterval(interval), 5 * 60 * 1000);

    return () => {
      supabase.removeChannel(channel);
      clearInterval(interval);
      clearTimeout(stop);
    };
  }, [router, eventId, realtime, authCookieName]);

  if (assets.length === 0) {
    return <p className="text-muted-foreground">No visual assets available.</p>;
  }

  const groupedByEventType = new Map<string, typeof assets>();
  for (const asset of assets) {
    const key = asset.eventType || "Other";
    if (!groupedByEventType.has(key)) groupedByEventType.set(key, []);
    groupedByEventType.get(key)!.push(asset);
  }

  return (
    <div className="space-y-8">
      {Array.from(groupedByEventType.entries()).map(
        ([eventType, groupAssets]) => (
          <div key={eventType} className="space-y-4">
            <h2 className="text-xl font-semibold">{eventType}</h2>
            {groupAssets.map((asset) => (
              <div key={asset.id}>
                <h3 className="text-lg font-medium mb-3">{asset.category}</h3>
                <div className="grid grid-cols-1 gap-4">
                  {asset.variants.map((variant) => (
                    <Card
                      key={variant.id}
                      className="flex flex-col sm:flex-row overflow-hidden cursor-pointer hover:ring-2 ring-primary/50 transition-all"
                      onClick={() => setSelected(variant)}
                    >
                      <div className="w-full sm:w-48 md:w-64 flex-shrink-0">
                        <AssetThumbnail variant={variant} />
                      </div>
                      <CardContent className="flex-1 p-4 flex flex-col justify-between">
                        <div className="space-y-1">
                          <p className="font-medium line-clamp-2">
                            {variant.name}
                          </p>
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <span>{variant.type}</span>
                            {variant.dimensions && (
                              <span className="rounded bg-muted px-1.5 py-0.5">
                                {variant.dimensions}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-2 mt-4 sm:mt-0">
                          <a
                            href={variant.downloadUrl}
                            download
                            target="_blank"
                            rel="noreferrer"
                            className={cn(
                              buttonVariants({ variant: "outline", size: "sm" })
                            )}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <Download className="w-4 h-4 mr-2" />
                            Download
                          </a>
                          {canDelete && onDeleteAsset && (
                            <DeleteAssetButton
                              assetId={variant.id}
                              eventId={eventId}
                              onDeleteAsset={onDeleteAsset}
                            />
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}
      <Lightbox variant={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
