import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Canvas, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, Html, Environment, useGLTF, Bounds, Line } from '@react-three/drei';
import * as THREE from 'three';
import { DefectMarkerData } from '../types';
import { withAccessToken } from '@/lib/mediaAuth';

// ── Severity Colors ──
const SEVERITY_COLORS: Record<string, string> = {
  critical: '#f43f5e',
  severe: '#f59e0b',
  moderate: '#eab308',
  minor: '#10b981',
  unknown: '#94a3b8',
};

function formatClassName(cls: string): string {
  return cls.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ── Shared ref store for OrbitControls ──
const controlsStore = { current: null as any };

// ── Model Component ──
interface ModelProps {
  url: string;
  layers?: { mesh: boolean; texture: boolean };
  onMeshReady?: (bbox: THREE.Box3, scene: THREE.Object3D) => void;
  onClick?: (e: any) => void;
  onPointerMove?: (e: any) => void;
}

function Model({ url, layers, onMeshReady, onClick, onPointerMove }: ModelProps) {
  const { scene } = useGLTF(url);
  const reported = useRef(false);

  React.useEffect(() => {
    if (scene) {
      scene.traverse((child: any) => {
        if (child.isMesh && child.material) {
          child.visible = layers ? layers.mesh : true;
          const processMat = (mat: any) => {
            mat.side = THREE.DoubleSide;
            if (mat._originalMap === undefined) mat._originalMap = mat.map || null;
            const showTexture = layers ? layers.texture : true;
            mat.map = showTexture ? mat._originalMap : null;
            if ('metalness' in mat) mat.metalness = 0.0;
            if ('roughness' in mat) mat.roughness = 0.95;
            if (mat.map) {
              mat.map.colorSpace = THREE.SRGBColorSpace;
              mat.map.anisotropy = 4;
            }
            if (mat.color) mat.color.setHex(0xffffff);
            mat.needsUpdate = true;
          };
          if (Array.isArray(child.material)) child.material.forEach(processMat);
          else processMat(child.material);
        }
      });

      if (!reported.current && onMeshReady) {
        reported.current = true;
        const bbox = new THREE.Box3().setFromObject(scene);
        onMeshReady(bbox, scene);
      }
    }
  }, [scene, layers, onMeshReady]);

  return <primitive object={scene} onClick={onClick} onPointerMove={onPointerMove} />;
}

// ── Camera Preset Handler Component ──
interface CameraPresetHandlerProps {
  preset: 'reset' | 'topdown' | null;
  onApplied: () => void;
  meshBBox: THREE.Box3 | null;
}

function CameraPresetHandler({ preset, onApplied, meshBBox }: CameraPresetHandlerProps) {
  const { camera } = useThree();

  useEffect(() => {
    if (!preset || !meshBBox) return;

    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    meshBBox.getCenter(center);
    meshBBox.getSize(size);
    const diagonal = size.length();
    const controls = controlsStore.current;

    if (preset === 'topdown') {
      camera.position.set(center.x, center.y + diagonal * 0.8, center.z);
      if (controls) {
        controls.target.set(center.x, center.y, center.z);
        controls.update();
      }
    } else if (preset === 'reset') {
      camera.position.set(center.x, center.y + diagonal * 0.5, center.z + diagonal * 0.5);
      if (controls) {
        controls.target.set(center.x, center.y, center.z);
        controls.update();
      }
    }

    onApplied();
  }, [preset, meshBBox, camera, onApplied]);

  return null;
}

// ── Camera Initializer Component (Custom Auto-Fit Closer View) ──
function CameraInitializer({ meshBBox }: { meshBBox: THREE.Box3 | null }) {
  const { camera } = useThree();
  const initialized = useRef(false);

  useEffect(() => {
    if (!meshBBox || initialized.current) return;
    initialized.current = true;
    
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    meshBBox.getCenter(center);
    meshBBox.getSize(size);
    const diagonal = size.length();
    const controls = controlsStore.current;
    
    // Zoom in closer to the road surface at a premium tilted angle
    camera.position.set(center.x, center.y + diagonal * 0.18, center.z + diagonal * 0.22);
    if (controls) {
      controls.target.copy(center);
      controls.update();
    }
  }, [meshBBox, camera]);

  return null;
}

// ── Camera Fly-to Controller ──
interface CameraControllerProps {
  targetPosition: [number, number, number] | null;
  enabled: boolean;
  minDist: number;
}

function CameraController({ targetPosition, enabled, minDist }: CameraControllerProps) {
  const { camera } = useThree();
  const isAnimating = useRef(false);
  const targetRef = useRef(new THREE.Vector3());
  const camTargetRef = useRef(new THREE.Vector3());
  const frameCount = useRef(0);

  useEffect(() => {
    if (targetPosition && enabled) {
      const [x, y, z] = targetPosition;
      targetRef.current.set(x, y, z);
      const dist = minDist * 1.5;
      camTargetRef.current.set(x + dist * 0.3, y + dist * 0.8, z + dist * 0.5);
      isAnimating.current = true;
      frameCount.current = 0;
    }
  }, [targetPosition, enabled, minDist]);

  useFrame(() => {
    if (!isAnimating.current) return;
    frameCount.current++;

    camera.position.lerp(camTargetRef.current, 0.06);

    const controls = controlsStore.current;
    if (controls && controls.target) {
      controls.target.lerp(targetRef.current, 0.06);
      controls.update();
    }

    if (camera.position.distanceTo(camTargetRef.current) < 0.1 || frameCount.current > 120) {
      isAnimating.current = false;
    }
  });

  return null;
}

// ── OrbitControls with shared ref + dynamic zoom limits ──
function SceneControls({ minDist }: { minDist: number }) {
  const ref = useRef<any>(null);

  useEffect(() => {
    if (ref.current) {
      controlsStore.current = ref.current;
    }
  }, [ref.current]);

  return (
    <OrbitControls
      ref={ref}
      makeDefault
      enableDamping
      dampingFactor={0.05}
      minDistance={minDist}
      maxDistance={500}
      maxPolarAngle={Math.PI * 0.85}
    />
  );
}

// ── Defect Markers with Raycasting ──
interface DefectMarkersProps {
  defects: DefectMarkerData[];
  activeMarker: number | null;
  onMarkerClick: (trackId: number) => void;
  meshBBox: THREE.Box3;
  activeFilters?: { classes: string[]; severities: string[] };
}

function DefectMarkers({ defects, activeMarker, onMarkerClick, meshBBox, activeFilters }: DefectMarkersProps) {
  const { scene } = useThree();
  const [positions, setPositions] = useState<Record<number, THREE.Vector3>>({});
  
  useEffect(() => {
    if (!meshBBox || defects.length === 0) return;
    
    const size = new THREE.Vector3();
    const center = new THREE.Vector3();
    meshBBox.getSize(size);
    meshBBox.getCenter(center);

    const diag = size.length() || 10;

    // Sort dimensions to find the major alignment axis of the 3D road/bridge
    const dims = [
      { axis: 'x' as const, len: size.x },
      { axis: 'y' as const, len: size.y },
      { axis: 'z' as const, len: size.z },
    ].sort((a, b) => b.len - a.len);

    const majorAxis = dims[0].axis;
    const majorLen = dims[0].len;
    
    const maxFrame = Math.max(...defects.map(d => d.frame_index), 1);
    
    const raycaster = new THREE.Raycaster();
    const newPositions: Record<number, THREE.Vector3> = {};
    
    const meshes: THREE.Object3D[] = [];
    scene.traverse((child) => {
      if ((child as THREE.Mesh).isMesh && child.visible) meshes.push(child);
    });

    if (meshes.length === 0) return;

    defects.forEach(defect => {
      if (activeFilters) {
        if (activeFilters.classes.length > 0 && !activeFilters.classes.includes(defect.class)) return;
        if (activeFilters.severities.length > 0 && !activeFilters.severities.includes(defect.severity)) return;
      }

      const t = defect.frame_index / maxFrame;
      // Use the detected image position to place the marker laterally on the
      // reconstructed corridor.  The previous implementation used only the
      // frame number, putting every defect on the centreline and making the
      // markers appear disconnected from the AI box.
      const bbox = Array.isArray(defect.bbox) && defect.bbox.length >= 4 ? defect.bbox : null;
      const imageCenterX = bbox ? (Number(bbox[0]) + Number(bbox[2])) / 2 : 0.5;
      const lateral = THREE.MathUtils.clamp(imageCenterX, 0, 1) - 0.5;
      const start = majorAxis === 'x' ? meshBBox.min.x : majorAxis === 'y' ? meshBBox.min.y : meshBBox.min.z;
      const projectedAxisValue = start + t * majorLen;
      
      const rayOrigin = new THREE.Vector3(center.x, center.y, center.z);
      let rayDir = new THREE.Vector3(0, -1, 0);

      if (majorAxis === 'x') {
        rayOrigin.x = projectedAxisValue;
        rayOrigin.z = center.z + lateral * size.z * 0.85;
        rayOrigin.y = meshBBox.max.y + diag * 0.1;
        rayDir = new THREE.Vector3(0, -1, 0);
      } else if (majorAxis === 'z') {
        rayOrigin.z = projectedAxisValue;
        rayOrigin.x = center.x + lateral * size.x * 0.85;
        rayOrigin.y = meshBBox.max.y + diag * 0.1;
        rayDir = new THREE.Vector3(0, -1, 0);
      } else {
        rayOrigin.y = projectedAxisValue;
        rayOrigin.x = center.x + lateral * size.x * 0.85;
        rayOrigin.z = meshBBox.max.z + diag * 0.1;
        rayDir = new THREE.Vector3(0, 0, -1);
      }

      raycaster.set(rayOrigin, rayDir);
      const intersects = raycaster.intersectObjects(meshes, true);
      
      if (intersects.length > 0) {
        const hit = intersects[0];
        const normal = hit.face ? hit.face.normal.clone().transformDirection(hit.object.matrixWorld) : new THREE.Vector3(0, 1, 0);
        newPositions[defect.track_id] = hit.point.clone().add(normal.multiplyScalar(diag * 0.005));
      } else {
        // Fallback to top surface of 3D mesh
        const fallback = new THREE.Vector3(center.x, meshBBox.max.y, center.z);
        if (majorAxis === 'x') fallback.x = projectedAxisValue;
        else if (majorAxis === 'z') fallback.z = projectedAxisValue;
        else fallback.y = projectedAxisValue;
        newPositions[defect.track_id] = fallback;
      }
    });

    setPositions(newPositions);
  }, [defects, meshBBox, scene, activeFilters]);

  // Dynamic proportional scale based on 3D mesh diagonal length
  const bboxSize = new THREE.Vector3();
  meshBBox?.getSize(bboxSize);
  const diag = bboxSize.length() || 10;
  
  // Sleek proportional pin size (0.3% of mesh diagonal)
  const pinRadius = Math.max(0.025, Math.min(0.16, diag * 0.0012));

  return (
    <group>
      {defects.map(defect => {
        const pos = positions[defect.track_id];
        if (!pos) return null;
        
        const isActive = activeMarker === defect.track_id;
        const color = SEVERITY_COLORS[defect.severity] || SEVERITY_COLORS.unknown;
        
        return (
          <group key={defect.track_id} position={pos}>
            {/* Sleek 3D Micro-Pin on Surface */}
            <mesh 
              onClick={(e) => {
                e.stopPropagation();
                onMarkerClick(defect.track_id);
              }}
              onPointerOver={(e) => {
                e.stopPropagation();
                document.body.style.cursor = 'pointer';
              }}
              onPointerOut={(e) => {
                e.stopPropagation();
                document.body.style.cursor = 'auto';
              }}
              position={[0, pinRadius * 0.8, 0]}
            >
              <sphereGeometry args={[isActive ? pinRadius * 1.2 : pinRadius, 16, 16]} />
              <meshStandardMaterial 
                color={color} 
                emissive={color} 
                emissiveIntensity={isActive ? 0.9 : 0.3} 
                roughness={0.2}
                metalness={0.8}
              />
            </mesh>
            
            {/* Ground Ring */}
            <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
              <ringGeometry args={[pinRadius * 0.8, pinRadius * (isActive ? 1.5 : 1.1), 24]} />
              <meshBasicMaterial color={color} side={THREE.DoubleSide} transparent opacity={isActive ? 0.8 : 0.4} />
            </mesh>

            {/* Active Marker Badge */}
            {isActive && (
              <Html position={[0, pinRadius * 4, 0]} center zIndexRange={[100, 0]}>
                <div className="bg-slate-900/95 backdrop-blur-md border border-white/20 px-3 py-2 rounded-xl shadow-2xl flex flex-col gap-1 items-center transform transition-all pointer-events-none w-max">
                  <div className="text-[10px] font-extrabold text-white uppercase tracking-wider flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full animate-ping" style={{ backgroundColor: color }} />
                    {formatClassName(defect.class)}
                  </div>
                  <div className="flex items-center gap-2 text-[9px] font-semibold">
                    <span className="px-1.5 py-0.5 rounded text-slate-900 font-bold" style={{ backgroundColor: color }}>
                      {defect.severity.toUpperCase()}
                    </span>
                    <span className="text-white/60">•</span>
                    <span className="text-white/80">Frame {defect.frame_index}</span>
                  </div>
                </div>
              </Html>
            )}
          </group>
        );
      })}
    </group>
  );
}

// ── Helper midpoint calculation for measurements ──
const getMidpoint = (p1: THREE.Vector3, p2: THREE.Vector3): [number, number, number] => {
  return [
    (p1.x + p2.x) / 2,
    (p1.y + p2.y) / 2,
    (p1.z + p2.z) / 2
  ];
};

// ── Main ThreeViewer ──
interface ThreeViewerProps {
  jobId: string | null;
  layers?: { mesh: boolean; texture: boolean };
  defects?: DefectMarkerData[];
  focusTrackId?: number | null;
  onMarkerClick?: (trackId: number) => void;
  activeFilters?: { classes: string[]; severities: string[] };

  measurementMode?: boolean;
  onMeasureDistance?: (distance: number | null) => void;
  scaleFactor?: number;
  lightIntensity?: number;
  cameraPreset?: 'reset' | 'topdown' | null;
  onCameraPresetApplied?: () => void;
}

export function ThreeViewer({
  jobId, 
  layers, 
  defects, 
  focusTrackId, 
  onMarkerClick, 
  activeFilters,
  measurementMode = false,
  onMeasureDistance,
  scaleFactor = 1.0,
  lightIntensity = 1.5,
  cameraPreset = null,
  onCameraPresetApplied
}: ThreeViewerProps) {
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  const [urlStatus, setUrlStatus] = useState<'loading' | 'success' | 'error'>('loading');
  const [meshBBox, setMeshBBox] = useState<THREE.Box3 | null>(null);
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const [dynamicMinDist, setDynamicMinDist] = useState(3);

  const [measurePoints, setMeasurePoints] = useState<THREE.Vector3[]>([]);
  const [hoverPoint, setHoverPoint] = useState<THREE.Vector3 | null>(null);

  useEffect(() => {
    if (!measurementMode) {
      setMeasurePoints([]);
      setHoverPoint(null);
      onMeasureDistance?.(null);
    }
  }, [measurementMode]);

  useEffect(() => {
    if (focusTrackId !== undefined && focusTrackId !== null) {
      setActiveMarker(focusTrackId);
    }
  }, [focusTrackId]);

  const flyToPosition = useMemo<[number, number, number] | null>(() => {
    if (activeMarker === null || !defects || !meshBBox) return null;
    const defect = defects.find(d => d.track_id === activeMarker);
    if (!defect) return null;

    const size = new THREE.Vector3();
    const center = new THREE.Vector3();
    meshBBox.getSize(size);
    meshBBox.getCenter(center);

    const dims = [
      { axis: 'x' as const, len: size.x },
      { axis: 'y' as const, len: size.y },
      { axis: 'z' as const, len: size.z },
    ].sort((a, b) => b.len - a.len);

    const majorAxis = dims[0].axis;
    const majorLen = dims[0].len;
    const maxFrame = Math.max(...defects.map(d => d.frame_index), 1);
    const t = defect.frame_index / maxFrame;

    const pos: [number, number, number] = [center.x, center.y, center.z];
    const axisIdx = majorAxis === 'x' ? 0 : majorAxis === 'y' ? 1 : 2;
    const start = majorAxis === 'x' ? meshBBox.min.x : majorAxis === 'y' ? meshBBox.min.y : meshBBox.min.z;
    pos[axisIdx] = start + t * majorLen;

    return pos;
  }, [activeMarker, defects, meshBBox]);

  useEffect(() => {
    if (!jobId) { setModelUrl(null); setUrlStatus('error'); return; }
    const fixedUrl = withAccessToken(`/api/crack/twin/files/${jobId}/3d_output/texturedMesh_fixed.glb`);
    const normalUrl = withAccessToken(`/api/crack/twin/files/${jobId}/3d_output/texturedMesh.glb`);
    setUrlStatus('loading');
    
    let isCancelled = false;

    const loadCachedOrFetchUrl = async (targetUrl: string): Promise<string | null> => {
      try {
        if ('caches' in window) {
          const cache = await caches.open('dt-3d-models-v1');
          const match = await cache.match(targetUrl);
          if (match) {
            const blob = await match.blob();
            return URL.createObjectURL(blob);
          }
        }
        const res = await fetch(targetUrl);
        if (res.ok) {
          if ('caches' in window) {
            const cache = await caches.open('dt-3d-models-v1');
            cache.put(targetUrl, res.clone());
          }
          const blob = await res.blob();
          return URL.createObjectURL(blob);
        }
      } catch (e) {
        console.warn('3D Cache Storage fallback:', e);
      }
      return targetUrl;
    };

    const resolveModel = async () => {
      try {
        const checkRes = await fetch(fixedUrl, { method: 'HEAD' });
        const target = checkRes.ok ? fixedUrl : normalUrl;
        const blobUrl = await loadCachedOrFetchUrl(target);
        if (!isCancelled) {
          if (blobUrl) {
            setModelUrl(blobUrl);
            setUrlStatus('success');
          } else {
            setUrlStatus('error');
          }
        }
      } catch (e) {
        if (!isCancelled) setUrlStatus('error');
      }
    };

    resolveModel();

    return () => {
      isCancelled = true;
    };
  }, [jobId]);

  const handleMeshReady = useCallback((bbox: THREE.Box3) => {
    setMeshBBox(bbox);
    const size = new THREE.Vector3();
    bbox.getSize(size);
    const diagonal = size.length();
    const computed = Math.max(diagonal * 0.05, 2);
    setDynamicMinDist(computed);
  }, []);

  const handleMarkerClick = useCallback((trackId: number) => {
    setActiveMarker(prev => prev === trackId ? null : trackId);
    onMarkerClick?.(trackId);
  }, [onMarkerClick]);

  const handleModelClick = (e: any) => {
    if (!measurementMode) return;
    e.stopPropagation();
    const point = e.point.clone();

    setMeasurePoints(prev => {
      if (prev.length >= 2) {
        return [point];
      } else {
        const next = [...prev, point];
        if (next.length === 2) {
          const rawDist = next[0].distanceTo(next[1]);
          onMeasureDistance?.(rawDist);
        }
        return next;
      }
    });
  };

  const handleModelPointerMove = (e: any) => {
    if (!measurementMode || measurePoints.length !== 1) return;
    e.stopPropagation();
    setHoverPoint(e.point.clone());
  };

  return (
    <div className="w-full h-full bg-slate-900 relative">
      <Canvas 
        frameloop="demand" 
        dpr={[1, 1.5]} 
        gl={{ antialias: true, powerPreference: 'high-performance' }}
        camera={{ position: [0, 50, 50], fov: 60 }}
      >
        <ambientLight intensity={lightIntensity} color="#ffffff" />
        <directionalLight position={[10, 20, 10]} intensity={lightIntensity} />
        <directionalLight position={[-10, -20, -10]} intensity={0.5} />
        <Environment preset="city" />

        {modelUrl && urlStatus === 'success' ? (
          <React.Suspense fallback={<Html center><div className="text-white text-xs bg-black/50 px-4 py-2 rounded-lg backdrop-blur-sm whitespace-nowrap">Đang tải mô hình GLB...</div></Html>}>
            <group>
              <CameraInitializer meshBBox={meshBBox} />
              <Model 
                url={modelUrl} 
                layers={layers} 
                onMeshReady={handleMeshReady} 
                onClick={handleModelClick}
                onPointerMove={handleModelPointerMove}
              />
              
              {!measurementMode && defects && defects.length > 0 && meshBBox && (
                <DefectMarkers 
                  defects={defects} 
                  activeMarker={activeMarker} 
                  onMarkerClick={handleMarkerClick} 
                  meshBBox={meshBBox}
                  activeFilters={activeFilters}
                />
              )}

              {measurePoints.length > 0 && (
                <group>
                  <mesh position={measurePoints[0]}>
                    <sphereGeometry args={[0.3, 16, 16]} />
                    <meshBasicMaterial color="#f43f5e" depthTest={false} />
                  </mesh>

                  {measurePoints.length === 2 && (
                    <mesh position={measurePoints[1]}>
                      <sphereGeometry args={[0.3, 16, 16]} />
                      <meshBasicMaterial color="#f43f5e" depthTest={false} />
                    </mesh>
                  )}

                  {measurePoints.length === 2 && (
                    <Line 
                      points={[measurePoints[0], measurePoints[1]]} 
                      color="#f43f5e" 
                      lineWidth={3}
                    />
                  )}

                  {measurePoints.length === 1 && hoverPoint && (
                    <Line 
                      points={[measurePoints[0], hoverPoint]} 
                      color="#f43f5e" 
                      lineWidth={2}
                    />
                  )}

                  {measurePoints.length === 2 && (
                    <Html position={getMidpoint(measurePoints[0], measurePoints[1])} center zIndexRange={[100, 0]}>
                      <div className="bg-rose-600 text-white font-bold text-[10px] px-2 py-1 rounded shadow-lg border border-white/20 whitespace-nowrap pointer-events-none select-none animate-fade-in">
                        {((measurePoints[0].distanceTo(measurePoints[1])) * scaleFactor).toFixed(2)}m
                      </div>
                    </Html>
                  )}
                </group>
              )}
            </group>
          </React.Suspense>
        ) : urlStatus === 'error' && modelUrl ? (
          <Html center>
            <div className="bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-3 rounded-lg text-sm text-center min-w-[200px] backdrop-blur-md">
              <p className="font-bold mb-1">Lỗi tải mô hình</p>
              <p className="text-xs">Không tìm thấy dữ liệu GLB.</p>
            </div>
          </Html>
        ) : urlStatus === 'loading' ? (
          <Html center><div className="text-white text-sm">Đang kiểm tra dữ liệu...</div></Html>
        ) : (
          <mesh><boxGeometry args={[1, 1, 1]} /><meshStandardMaterial color="gray" wireframe /></mesh>
        )}

        {onCameraPresetApplied && (
          <CameraPresetHandler 
            preset={cameraPreset} 
            onApplied={onCameraPresetApplied} 
            meshBBox={meshBBox}
          />
        )}

        <CameraController targetPosition={flyToPosition} enabled={!measurementMode && focusTrackId !== null && focusTrackId !== undefined} minDist={dynamicMinDist} />
        <SceneControls minDist={dynamicMinDist} />
      </Canvas>

      <div className="absolute bottom-3 left-3 bg-black/60 backdrop-blur-sm border border-white/10 px-3 py-2 rounded-lg text-[10px] text-slate-300 space-y-0.5 pointer-events-none shadow-md z-10">
        {measurementMode ? (
          <>
            <p>📌 <span className="text-rose-400 font-bold">Chế độ Thước đo 3D:</span></p>
            <p>🖱️ <span className="text-white font-medium">Click điểm thứ 1:</span> Bắt đầu</p>
            <p>🖱️ <span className="text-white font-medium">Click điểm thứ 2:</span> Kết thúc</p>
          </>
        ) : (
          <>
            <p>🖱️ <span className="text-white font-medium">Chuột trái:</span> Xoay mô hình</p>
            <p>🖱️ <span className="text-white font-medium">Chuột phải:</span> Kéo di chuyển</p>
            <p>⚙️ <span className="text-white font-medium">Cuộn chuột:</span> Thu phóng</p>
            <p>📌 <span className="text-white font-medium">Click marker:</span> Xem vết nứt</p>
          </>
        )}
      </div>

      {!measurementMode && defects && defects.length > 0 && (
        <div className="absolute top-3 right-3 bg-black/60 backdrop-blur-sm border border-white/10 px-3 py-1.5 rounded-lg text-xs text-white shadow-md pointer-events-none z-10">
          📌 {defects.length} vết nứt trên mô hình
        </div>
      )}
    </div>
  );
}
