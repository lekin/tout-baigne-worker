// Partner-safe visual-asset shape — materialized into events.raw.assets by
// syncEventMap (buildAssets from Admin's repositories) and surfaced through
// the backstage RPCs.

export interface AssetVariant {
  id: string;
  name: string;
  type: string;
  dimensions?: string;
  mime: string;
  url: string;
  thumbnailUrl?: string;
  downloadUrl: string;
  roomTarget?: string;
}

export interface Asset {
  id: string;
  eventType?: string;
  category: string;
  variants: AssetVariant[];
}
