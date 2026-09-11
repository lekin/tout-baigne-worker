"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { WeatherHour } from "./types";

export function WeatherChart({ hours }: { hours: WeatherHour[] }) {
  const data = hours.map((h) => ({
    time: `${new Date(h.time).getHours()}h`,
    temperature: Math.round(h.temperature * 10) / 10,
    precipitation: h.precipitation,
  }));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <ComposedChart
        data={data}
        margin={{ top: 8, right: 0, bottom: 0, left: -18 }}
      >
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="rgba(148,163,184,0.15)"
          vertical={false}
        />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 11, fill: "#94a3b8" }}
          tickLine={false}
          axisLine={{ stroke: "rgba(148,163,184,0.2)" }}
        />
        <YAxis
          yAxisId="temp"
          tick={{ fontSize: 11, fill: "#94a3b8" }}
          tickLine={false}
          axisLine={false}
          unit="°"
          domain={["auto", "auto"]}
        />
        <YAxis
          yAxisId="precip"
          orientation="right"
          tick={{ fontSize: 11, fill: "#94a3b8" }}
          tickLine={false}
          axisLine={false}
          unit="mm"
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--popover)",
            border: "1px solid rgba(148,163,184,0.2)",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar
          yAxisId="precip"
          dataKey="precipitation"
          name="Précipitations (mm)"
          fill="#38bdf8"
          fillOpacity={0.35}
        />
        <Line
          yAxisId="temp"
          type="monotone"
          dataKey="temperature"
          name="Température (°C)"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
