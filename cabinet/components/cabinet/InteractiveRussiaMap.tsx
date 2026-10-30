'use client';

import { Fragment } from 'react';
import { CircleMarker, MapContainer, TileLayer, Tooltip, ZoomControl } from 'react-leaflet';

type MarkerTone = 'current' | 'positive' | 'negative' | 'neutral';

type MapRow = {
  row: {
    name: string;
  };
  coordinates: [number, number];
  value: number;
  valueLabel: string;
};

type Props = {
  mapRows: MapRow[];
  mapMaxValue: number;
  geoValueMode: 'period' | 'delta';
  onHover: (name: string) => void;
};

function toneForValue(value: number, geoValueMode: 'period' | 'delta'): MarkerTone {
  if (geoValueMode === 'period') return 'current';
  if (value > 0) return 'positive';
  if (value < 0) return 'negative';
  return 'neutral';
}

function markerColors(tone: MarkerTone) {
  if (tone === 'positive') {
    return { fill: '#16a34a', glow: 'rgba(22, 163, 74, 0.28)' };
  }
  if (tone === 'negative') {
    return { fill: '#dc2626', glow: 'rgba(220, 38, 38, 0.28)' };
  }
  if (tone === 'neutral') {
    return { fill: '#64748b', glow: 'rgba(100, 116, 139, 0.24)' };
  }
  return { fill: '#6d5df6', glow: 'rgba(109, 93, 246, 0.28)' };
}

export function InteractiveRussiaMap({ mapRows, mapMaxValue, geoValueMode, onHover }: Props) {
  return (
    <MapContainer
      center={[61, 95]}
      zoom={3}
      minZoom={2}
      maxZoom={6}
      zoomControl={false}
      attributionControl={false}
      scrollWheelZoom
      dragging
      doubleClickZoom
      className="workbench-leaflet-map"
      maxBounds={[
        [39, 18],
        [78, 180],
      ]}
      maxBoundsViscosity={1}
    >
      <ZoomControl position="bottomright" />
      <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

      {mapRows.map((item) => {
        const radius = 7 + (Math.abs(item.value) / mapMaxValue) * 12;
        const tone = toneForValue(item.value, geoValueMode);
        const color = markerColors(tone);
        const position: [number, number] = [item.coordinates[1], item.coordinates[0]];

        return (
          <Fragment key={item.row.name}>
            <CircleMarker
              center={position}
              radius={radius}
              pathOptions={{
                color: color.fill,
                fillColor: color.fill,
                fillOpacity: 0.82,
                weight: 2,
                opacity: 0.9,
              }}
              eventHandlers={{
                mouseover: () => onHover(item.row.name),
                click: () => onHover(item.row.name),
              }}
            >
              <Tooltip permanent direction="top" offset={[0, -8]} className="workbench-leaflet-label">
                {item.row.name}
              </Tooltip>
            </CircleMarker>
            <CircleMarker
              center={position}
              radius={0.1}
              interactive={false}
              pathOptions={{
                color: 'transparent',
                fillColor: 'transparent',
                fillOpacity: 0,
                opacity: 0,
                weight: 0,
              }}
            >
              <Tooltip permanent direction="bottom" offset={[0, 12]} className={`workbench-leaflet-value workbench-leaflet-value--${tone}`}>
                {item.valueLabel}
              </Tooltip>
            </CircleMarker>
            <CircleMarker
              center={position}
              radius={radius + 8}
              interactive={false}
              pathOptions={{
                color: color.glow,
                fillColor: color.glow,
                fillOpacity: 0.24,
                opacity: 0.2,
                weight: 1,
              }}
            />
          </Fragment>
        );
      })}
    </MapContainer>
  );
}
