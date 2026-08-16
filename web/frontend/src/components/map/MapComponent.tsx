'use client';

import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, CircleMarker, useMap, Polyline } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Incident } from '@/types';
import { format } from 'date-fns';

// Map markers setup
const createMarkerIcon = (color: string) => {
  return L.divIcon({
    className: 'custom-leaflet-marker',
    html: `<div style="background-color: ${color}; width: 20px; height: 20px; border-radius: 50%; border: 3px solid white; box-shadow: 0 0 5px rgba(0,0,0,0.5);"></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  });
};



const colors: Record<string, string> = {
  critical: '#ef4444',
  warning: '#eab308',
  resolved: '#10b981',
};

// Map controller for flyTo effect
function MapController({ selectedIncident, selectedSegment }: { selectedIncident: Incident | null, selectedSegment: any | null }) {
  const map = useMap();
  
  useEffect(() => {
    if (selectedIncident) {
      map.flyTo([selectedIncident.lat, selectedIncident.lng], 16, {
        duration: 1.5,
        easeLinearity: 0.25
      });
    } else if (selectedSegment && selectedSegment.start_gps) {
      map.flyTo([selectedSegment.start_gps.lat, selectedSegment.start_gps.lng], 16, {
        duration: 1.5,
        easeLinearity: 0.25
      });
    }
  }, [selectedIncident, selectedSegment, map]);
  
  return null;
}

interface MapComponentProps {
  incidents: Incident[];
  selectedIncidentId?: string | null;
  onSelectIncident?: (id: string) => void;
  segments?: any[];
  selectedSegmentId?: string | null;
  onSelectSegment?: (segment: any) => void;
}

export default function MapComponent({
  incidents,
  selectedIncidentId,
  onSelectIncident,
  segments = [],
  selectedSegmentId = null,
  onSelectSegment,
}: MapComponentProps) {
  const [mounted, setMounted] = useState(false);
  const [userCenter, setUserCenter] = useState<[number, number] | null>(null);
  const [mapStyle, setMapStyle] = useState<'light' | 'dark' | 'satellite'>('light');

  useEffect(() => {
    setMounted(true);
    if (typeof window !== 'undefined' && 'geolocation' in navigator) {
      try {
        navigator.geolocation.getCurrentPosition(
          (position) => {
            if (position?.coords?.latitude && position?.coords?.longitude) {
              setUserCenter([position.coords.latitude, position.coords.longitude]);
            }
          },
          (error) => {
            // Geolocation error/blocked silently handled
          },
          { enableHighAccuracy: false, timeout: 2000, maximumAge: 300000 }
        );
      } catch {
        // Geolocation policy blocked
      }
    }
  }, []);

  if (!mounted) return <div className="w-full h-full bg-slate-100 animate-pulse rounded-xl" />;

  // Centering the viewport: Default to Vietnam center if no GPS/incidents
  let mapCenter: [number, number] = [16.047079, 108.206230];
  let defaultZoom = 6;

  if (incidents && incidents.length > 0 && incidents[0].lat && incidents[0].lng) {
    mapCenter = [incidents[0].lat, incidents[0].lng];
    defaultZoom = 15;
  } else if (userCenter) {
    mapCenter = userCenter;
    defaultZoom = 13;
  }

  const selectedIncident = incidents.find(i => i.id === selectedIncidentId) || null;
  const selectedSegment = segments.find(s => s.segment_id === selectedSegmentId) || null;

  const tileUrls = {
    light: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    satellite: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  };

  const tileAttributions = {
    light: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    dark: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap',
    satellite: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
  };

  return (
    <div className="w-full h-full rounded-xl overflow-hidden shadow-2xl relative z-0">
      {/* Map Control Floating Panel */}
      <div className="absolute top-3 right-3 z-[1000] bg-white/90 backdrop-blur-md border border-slate-200 rounded-xl p-1.5 flex items-center gap-1 shadow-xl">
        <button
          onClick={() => setMapStyle('light')}
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${
            mapStyle === 'light' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
          }`}
        >
          ☀️ Sáng (OpenStreetMap)
        </button>
        <button
          onClick={() => setMapStyle('dark')}
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${
            mapStyle === 'dark' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
          }`}
        >
          🌙 Tối
        </button>
        <button
          onClick={() => setMapStyle('satellite')}
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 ${
            mapStyle === 'satellite' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
          }`}
        >
          🛰️ Vệ tinh
        </button>
      </div>

      <MapContainer
        center={mapCenter}
        zoom={defaultZoom}
        style={{ width: '100%', height: '100%', background: mapStyle === 'dark' ? '#0f172a' : '#f8fafc' }}
        scrollWheelZoom={true}
        zoomControl={false}
      >
        <TileLayer
          key={mapStyle}
          attribution={tileAttributions[mapStyle]}
          url={tileUrls[mapStyle]}
        />
        {mapStyle === 'satellite' && (
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png"
          />
        )}

        {/* Controller for automatic pans */}
        <MapController selectedIncident={selectedIncident} selectedSegment={selectedSegment} />



        {/* --- Segments --- */}
        {segments.filter(seg => seg.start_gps && seg.end_gps).map((seg) => {
          const pci = Number.isFinite(seg.pci_current) ? seg.pci_current : null;
          let color = '#64748b';
          if (pci !== null && pci < 55) {
            color = '#ef4444'; // Red
          } else if (pci !== null && pci < 85) {
            color = '#f59e0b'; // Yellow
          } else if (pci !== null) {
            color = '#10b981';
          }
          
          const isSelected = seg.segment_id === selectedSegmentId;
          
          return (
            <Polyline
              key={seg.segment_id}
              positions={[
                [seg.start_gps.lat, seg.start_gps.lng],
                [seg.end_gps.lat, seg.end_gps.lng]
              ]}
              pathOptions={{
                color: color,
                weight: isSelected ? 8 : 4,
                opacity: isSelected ? 0.9 : 0.6
              }}
              eventHandlers={{
                click: () => onSelectSegment?.(seg)
              }}
            >
              <Popup>
                <div className="p-2 max-w-[220px] font-sans">
                  <h3 className="font-bold text-slate-800 text-xs mb-1">Đoạn đường: {seg.name}</h3>
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className="text-[10px] text-slate-400 font-bold uppercase">Chỉ số PCI:</span>
                    <span className={`text-xs font-black px-1.5 py-0.5 rounded ${
                      pci === null ? 'bg-slate-50 text-slate-500' :
                      pci >= 85 ? 'bg-emerald-50 text-emerald-600' :
                      pci >= 55 ? 'bg-amber-50 text-amber-600' :
                      'bg-red-50 text-red-600'
                    }`}>{pci === null ? 'Chưa đo' : pci.toFixed(1)}</span>
                  </div>
                  <p className="text-[10px] text-slate-500 mt-1.5">Kết cấu: {seg.structural_type || 'Chưa cập nhật'}</p>
                  <p className="text-[10px] text-slate-400 mt-1">Làn: {seg.lane || 'Chưa cập nhật'}</p>
                </div>
              </Popup>
            </Polyline>
          );
        })}

        {/* --- Incidents --- */}
        {incidents.filter(inc => Number.isFinite(inc.lat) && Number.isFinite(inc.lng)).map((inc) => (
          <Marker
            key={inc.id}
            position={[inc.lat, inc.lng]}
            icon={createMarkerIcon(colors[inc.severity])}
            eventHandlers={{
              click: () => onSelectIncident?.(inc.id),
            }}
            zIndexOffset={inc.id === selectedIncidentId ? 500 : 100}
          >
             <Popup>
                <div className="p-1 max-w-[200px]">
                    <h3 className="font-bold text-slate-800 text-sm mb-1 line-clamp-2">{inc.title}</h3>
                    <p className="text-xs text-slate-500 line-clamp-1">{inc.address}</p>
                    <p className="text-[10px] text-slate-400 mt-2">
                        Phát hiện: {(() => {
                          if (!inc.detected_at) return '--/--/----';
                          try {
                            const d = new Date(inc.detected_at);
                            return isNaN(d.getTime()) ? '--/--/----' : format(d, 'dd/MM/yyyy HH:mm');
                          } catch { return '--/--/----'; }
                        })()}
                    </p>
                </div>
            </Popup>
          </Marker>
        ))}

        {selectedIncidentId && (() => {
             const selected = incidents.find(i => i.id === selectedIncidentId);
             if (selected) {
                 return (
                     <CircleMarker center={[selected.lat, selected.lng]} radius={30} pathOptions={{color: colors[selected.severity], fillColor: colors[selected.severity], fillOpacity: 0.2, weight: 1}} />
                 );
             }
             return null;
        })()}

      </MapContainer>
      
       <style dangerouslySetInnerHTML={{__html: `
        .leaflet-container {
            font-family: inherit;
        }
        .leaflet-popup-content-wrapper {
            border-radius: 12px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.2);
            padding: 4px;
        }
        .leaflet-popup-content {
             margin: 8px 12px;
        }
      `}} />
    </div>
  );
}
