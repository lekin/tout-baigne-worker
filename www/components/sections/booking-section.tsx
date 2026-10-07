const BOOKING_FORM_URL =
  process.env.BOOKING_FORM_URL ??
  "https://airtable.com/appwJEY0yxNKlphA9/pagUGtGoqcMGnnfTo/form";

export function BookingSection() {
  return (
    <section className="booking-section">
      <div className="container-3" style={{ textAlign: "center" }}>
        <h2 className="booking-heading">
          Festival, private,
          <br />
          mi-temps du Superbowl ?
        </h2>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <a
            href={BOOKING_FORM_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="booking-button"
          >
            INFO BOOKING 📌
          </a>
        </div>
      </div>
    </section>
  );
}
