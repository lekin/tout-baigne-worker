export function TeaserSection({ youtubeId }: { youtubeId: string }) {
  return (
    <section className="teaser pattern-bg">
      <div className="container-3">
        <div className="teaser-embed">
          <iframe
            width="100%"
            height="400"
            src={`https://www.youtube.com/embed/${youtubeId}`}
            title="YouTube video player"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
          />
        </div>
      </div>
    </section>
  );
}
