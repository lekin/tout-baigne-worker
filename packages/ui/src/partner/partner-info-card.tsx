import { Card, CardContent, CardHeader, CardTitle } from "../ui/card"

export interface PartnerStaffItem {
  name: string
  role?: string | null
}

export interface PartnerRoom {
  name: string
  event_type?: string | null
  technical_title?: string | null
  technical_notes?: string | null
  staff?: PartnerStaffItem[] | null
}

export interface PartnerInfo {
  venue_name?: string | null
  rooms?: PartnerRoom[] | null
  consignes?: string | null
  invitations?: number | null
  run_sheet?: string | null
}

const TIME_RE =
  /^(?:(\d{2}:\d{2}(?:\s*[–-]\s*\d{2}:\d{2})?)\s+(.+))|(?:(.+?)\s+(\d{2}:\d{2}(?:\s*[–-]\s*\d{2}:\d{2})?))$/

function HorairesBlock({ lines }: { lines: string[] }) {
  return (
    <ul className="space-y-1 text-sm">
      {lines.map((line, i) => {
        const trimmed = line.trim()
        if (!trimmed) return null
        const match = trimmed.match(TIME_RE)
        if (match) {
          if (match[1]) {
            // time first: "00:00-06:00 Chrono DJs"
            return (
              <li key={i} className="flex gap-3">
                <span className="font-medium tabular-nums whitespace-nowrap">
                  {match[1]}
                </span>
                <span className="text-muted-foreground">{match[2]}</span>
              </li>
            )
          }
          // label first: "DOORS 00:00"
          return (
            <li key={i} className="flex gap-3">
              <span className="font-medium">{match[3]}</span>
              <span className="tabular-nums whitespace-nowrap text-muted-foreground">
                {match[4]}
              </span>
            </li>
          )
        }
        return (
          <li key={i} className="text-muted-foreground">
            {trimmed}
          </li>
        )
      })}
    </ul>
  )
}

function NotesBlock({ text }: { text: string }) {
  const blocks = text.split(/\n\s*\n/)
  return (
    <div className="space-y-3">
      {blocks.map((block, i) => {
        const lines = block
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean)
        if (lines.length === 0) return null
        const [first, ...rest] = lines

        if (first.toUpperCase() === "HORAIRES") {
          return (
            <div key={i}>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                Horaires
              </p>
              <HorairesBlock lines={rest} />
            </div>
          )
        }

        return (
          <div key={i} className="space-y-1">
            {lines.map((line, j) => {
              const colonMatch = line.match(/^(.*?)\s*:\s*(.*)$/)
              if (colonMatch) {
                return (
                  <p key={j} className="text-sm text-muted-foreground">
                    <span className="font-medium text-foreground">
                      {colonMatch[1]}
                    </span>
                    {" : "}
                    {colonMatch[2]}
                  </p>
                )
              }
              return (
                <p key={j} className="text-sm text-muted-foreground">
                  {line}
                </p>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

function RoomPartnerSection({ room }: { room: PartnerRoom }) {
  return (
    <div className="border-b last:border-0 pb-5 last:pb-0 mb-5 last:mb-0">
      <h3 className="text-sm font-bold uppercase tracking-wide">{room.name}</h3>
      <div className="flex items-center gap-2 mt-0.5 mb-3">
        {room.event_type && (
          <span className="text-xs text-muted-foreground">
            {room.event_type}
          </span>
        )}
        {room.technical_title && !room.technical_notes && (
          <span className="text-xs font-medium text-muted-foreground">
            {room.technical_title}
          </span>
        )}
      </div>

      {room.technical_notes && <NotesBlock text={room.technical_notes} />}

      {room.staff && room.staff.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Staff
          </p>
          <ul className="space-y-1 text-sm">
            {room.staff.map((s, i) => (
              <li key={i} className="flex justify-between">
                <span className="text-muted-foreground">{s.name}</span>
                {s.role && (
                  <span className="text-muted-foreground">{s.role}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function PartnerInfoCard({ info }: { info: PartnerInfo }) {
  const rooms = info.rooms ?? []
  const hasRoomInfo = rooms.some(
    (r) => r.technical_notes || r.technical_title || (r.staff && r.staff.length > 0)
  )
  const hasAutre = info.consignes || info.invitations != null

  if (!hasRoomInfo && !hasAutre && !info.run_sheet) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          Feuille de route{info.venue_name ? ` — ${info.venue_name}` : ""}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        {info.run_sheet && (
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">
            {info.run_sheet}
          </p>
        )}

        {hasRoomInfo && (
          <div>
            {rooms.map((room, i) => (
              <RoomPartnerSection key={i} room={room} />
            ))}
          </div>
        )}

        {hasAutre && (
          <div className="border-t pt-4">
            <h3 className="text-sm font-bold uppercase tracking-wide mb-2">
              Autre
            </h3>
            {info.invitations != null && !info.consignes && (
              <p className="text-sm text-muted-foreground">
                <span className="font-medium text-foreground">
                  Nombre d&apos;invitations
                </span>
                {" : "}
                {info.invitations}
              </p>
            )}
            {info.consignes && <NotesBlock text={info.consignes} />}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
