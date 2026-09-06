<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

const host = ref<HTMLDivElement | null>(null)
let dispose: (() => void) | undefined
let disposed = false

onMounted(async () => {
  if (!('WebGLRenderingContext' in window)) return
  const THREE = await import('three').catch(() => null)
  if (!THREE) return
  if (disposed || !host.value) return

  const element = host.value
  const surface = element.parentElement ?? element
  let renderer: InstanceType<typeof THREE.WebGLRenderer>
  try {
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: false, powerPreference: 'low-power' })
  } catch {
    return
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5))
  element.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(52, 1, 1, 300)
  camera.position.z = 80
  const geometry = new THREE.BufferGeometry()
  const positions = new Float32Array(420 * 3)
  let seed = 29
  const random = () => {
    seed = (seed * 16807) % 2147483647
    return (seed - 1) / 2147483646
  }
  for (let index = 0; index < positions.length; index += 3) {
    positions[index] = (random() - 0.5) * 360
    positions[index + 1] = (random() - 0.5) * 220
    positions[index + 2] = -random() * 180
  }
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  const material = new THREE.PointsMaterial({ size: 0.16, transparent: true, opacity: 0.3, depthWrite: false })
  const stars = new THREE.Points(geometry, material)
  scene.add(stars)

  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  const finePointer = window.matchMedia('(pointer: fine)')
  let frame = 0
  let visible = true
  let contextLost = false
  let lastTime = 0
  let elapsed = 0
  let pointerX = 0
  let pointerY = 0

  function draw(time = 0): void {
    frame = 0
    if (disposed || contextLost || !visible || document.hidden) return
    const delta = lastTime ? Math.min((time - lastTime) / 1000, 0.05) : 0
    lastTime = time
    if (!motion.matches && finePointer.matches) {
      elapsed += delta
      camera.position.x += (pointerX * 5 - camera.position.x) * 0.045
      camera.position.y += (pointerY * 3 - camera.position.y) * 0.045
      stars.rotation.z = Math.sin(elapsed * 0.045) * 0.006
    }
    renderer.render(scene, camera)
    if (!motion.matches && finePointer.matches) frame = requestAnimationFrame(draw)
  }

  function schedule(): void {
    cancelAnimationFrame(frame)
    lastTime = 0
    frame = requestAnimationFrame(draw)
  }

  function resize(): void {
    const { width, height } = element.getBoundingClientRect()
    if (!width || !height) return
    renderer.setSize(width, height)
    camera.aspect = width / height
    camera.updateProjectionMatrix()
    schedule()
  }

  function pointerMove(event: PointerEvent): void {
    const bounds = surface.getBoundingClientRect()
    pointerX = ((event.clientX - bounds.left) / bounds.width - 0.5) * 2
    pointerY = -((event.clientY - bounds.top) / bounds.height - 0.5) * 2
  }

  function pointerLeave(): void { pointerX = 0; pointerY = 0 }
  function updateTheme(): void {
    material.color.setStyle(getComputedStyle(element).getPropertyValue('--fg-canvas-ink').trim())
    schedule()
  }
  function onContextLost(event: Event): void {
    event.preventDefault()
    contextLost = true
    cancelAnimationFrame(frame)
  }
  function onContextRestored(): void { contextLost = false; resize() }

  const resizeObserver = new ResizeObserver(resize)
  resizeObserver.observe(element)
  const intersectionObserver = new IntersectionObserver(([entry]) => {
    visible = entry?.isIntersecting ?? false
    schedule()
  })
  intersectionObserver.observe(element)
  const themeObserver = new MutationObserver(updateTheme)
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'data-theme'] })
  surface.addEventListener('pointermove', pointerMove, { passive: true })
  surface.addEventListener('pointerleave', pointerLeave)
  document.addEventListener('visibilitychange', schedule)
  motion.addEventListener('change', schedule)
  finePointer.addEventListener('change', schedule)
  renderer.domElement.addEventListener('webglcontextlost', onContextLost)
  renderer.domElement.addEventListener('webglcontextrestored', onContextRestored)
  updateTheme()
  resize()

  dispose = () => {
    cancelAnimationFrame(frame)
    resizeObserver.disconnect()
    intersectionObserver.disconnect()
    themeObserver.disconnect()
    surface.removeEventListener('pointermove', pointerMove)
    surface.removeEventListener('pointerleave', pointerLeave)
    document.removeEventListener('visibilitychange', schedule)
    motion.removeEventListener('change', schedule)
    finePointer.removeEventListener('change', schedule)
    renderer.domElement.removeEventListener('webglcontextlost', onContextLost)
    renderer.domElement.removeEventListener('webglcontextrestored', onContextRestored)
    geometry.dispose()
    material.dispose()
    renderer.dispose()
    renderer.domElement.remove()
  }
})

onBeforeUnmount(() => { disposed = true; dispose?.() })
</script>

<template><div ref="host" class="cosmic-backdrop" aria-hidden="true" data-test="cosmic-backdrop"></div></template>

<style scoped>
.cosmic-backdrop { position: absolute; inset: 0; overflow: hidden; pointer-events: none; }
.cosmic-backdrop :deep(canvas) { display: block; width: 100%; height: 100%; }
</style>
