import { useEffect, useRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

/**
 * three.js viewer for the returned contact-frame GLB (UI.md §5).
 * Rotate/zoom via OrbitControls (pan disabled); mesh auto-framed with a 3/4
 * front default camera. The payload declares up_axis "Y" / units meters,
 * matching three.js conventions — no implicit axis flip is applied.
 */
export function GlbViewer({ glbData }: { glbData: ArrayBuffer }) {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100);

    scene.add(new THREE.HemisphereLight(0xdfeaff, 0x2a2620, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(2, 4, 3);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x88aaff, 0.8);
    rim.position.set(-3, 2, -2);
    scene.add(rim);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enablePan = false;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    let disposed = false;
    let ground: THREE.Object3D | null = null;

    const loader = new GLTFLoader();
    loader.parse(
      glbData.slice(0),
      "",
      (gltf) => {
        if (disposed) return;
        const model = gltf.scene;
        scene.add(model);

        // Auto-frame: fit the camera to the model's bounding sphere, 3/4 front.
        const box = new THREE.Box3().setFromObject(model);
        const center = box.getCenter(new THREE.Vector3());
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        const fitDist = (sphere.radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.15;
        const dir = new THREE.Vector3(0.8, 0.35, 1).normalize();
        camera.position.copy(center).addScaledVector(dir, fitDist);
        camera.near = fitDist / 100;
        camera.far = fitDist * 10;
        camera.updateProjectionMatrix();
        controls.target.copy(center);
        controls.minDistance = sphere.radius * 0.8;
        controls.maxDistance = fitDist * 3;
        controls.update();

        // Subtle ground disc under the feet for spatial grounding.
        const groundMesh = new THREE.Mesh(
          new THREE.CircleGeometry(sphere.radius * 1.2, 48),
          new THREE.MeshBasicMaterial({ color: 0x2f4f46, transparent: true, opacity: 0.35 }),
        );
        groundMesh.rotation.x = -Math.PI / 2;
        groundMesh.position.y = box.min.y + 0.001;
        groundMesh.position.x = center.x;
        groundMesh.position.z = center.z;
        scene.add(groundMesh);
        ground = groundMesh;
      },
      (err) => {
        console.error("[GlbViewer] failed to parse GLB", err);
      },
    );

    const resize = () => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (w === 0 || h === 0) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(mount);

    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      controls.update();
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          mats.forEach((m) => m.dispose());
        }
      });
      if (ground) scene.remove(ground);
      renderer.dispose();
      mount.removeChild(renderer.domElement);
    };
  }, [glbData]);

  return <div className="glb-viewer" ref={mountRef} aria-label="Rotatable 3D reconstruction of your serve at contact" />;
}
