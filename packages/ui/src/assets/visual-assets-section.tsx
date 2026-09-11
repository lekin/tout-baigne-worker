import type { ReactNode } from "react";
import { AssetGallery } from "./asset-gallery";
import type { Asset } from "./types";

// Visual assets section — same look in Admin and Backstage.
// Admin: canManage + actions (generate buttons) + onDeleteAsset + realtime.
// Backstage: read-only — just title + gallery.
export function VisualAssetsSection({
  eventId,
  assets,
  canManage = false,
  actions,
  onDeleteAsset,
  realtime,
  authCookieName,
}: {
  eventId: string;
  assets: Asset[];
  canManage?: boolean;
  /** Server-rendered action buttons (generate, …) shown when canManage. */
  actions?: ReactNode;
  onDeleteAsset?: (
    assetId: string,
    eventId: string
  ) => Promise<{ success: boolean; error?: string }>;
  realtime?: { table: string; filterColumn?: string };
  authCookieName?: string;
}) {
  return (
    <section>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">Visual assets</h2>
        {canManage && actions && (
          <div className="flex items-center gap-2">{actions}</div>
        )}
      </div>
      <AssetGallery
        assets={assets}
        eventId={eventId}
        canDelete={canManage}
        onDeleteAsset={onDeleteAsset}
        realtime={realtime}
        authCookieName={authCookieName}
      />
    </section>
  );
}
