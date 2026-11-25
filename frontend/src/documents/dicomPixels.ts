import type { DicomMetadata } from './api';

/** Raw stored values of one frame (after rescale slope/intercept), uncompressed monochrome only. */
export function decodeFrame(buffer: ArrayBuffer, meta: DicomMetadata, frame: number): Float32Array {
  const { rows, columns, bits_allocated: bits, pixel_offset: offset } = meta;
  if (!rows || !columns || !bits || offset == null || !meta.viewable) throw new Error('DICOM nesuportat pentru vizualizare');
  const count = rows * columns;
  const bytes = bits / 8;
  const start = offset + frame * count * bytes;
  if (frame < 0 || frame >= meta.frames || start + count * bytes > buffer.byteLength) throw new Error('Cadru în afara fișierului');
  const view = new DataView(buffer, start, count * bytes);
  const signed = meta.pixel_representation === 1;
  const slope = meta.rescale_slope ?? 1;
  const intercept = meta.rescale_intercept ?? 0;
  const out = new Float32Array(count);
  for (let index = 0; index < count; index += 1) {
    const raw = bits === 8 ? (signed ? view.getInt8(index) : view.getUint8(index)) : (signed ? view.getInt16(index * 2, true) : view.getUint16(index * 2, true));
    out[index] = raw * slope + intercept;
  }
  return out;
}

export function valueRange(values: Float32Array): { min: number; max: number } {
  let min = Infinity; let max = -Infinity;
  for (let index = 0; index < values.length; index += 1) { const value = values[index]; if (value < min) min = value; if (value > max) max = value; }
  return { min, max };
}

/** DICOM linear window/level to gray RGBA (PS3.3 C.11.2.1.2). MONOCHROME1 is displayed inverted. */
export function windowToRgba(values: Float32Array, center: number, width: number, invert: boolean): Uint8ClampedArray {
  const out = new Uint8ClampedArray(values.length * 4);
  const w = Math.max(1, width);
  const low = center - 0.5 - (w - 1) / 2;
  const high = center - 0.5 + (w - 1) / 2;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    let gray: number;
    if (value <= low) gray = 0;
    else if (value > high) gray = 255;
    else gray = w <= 1 ? 255 : ((value - (center - 0.5)) / (w - 1) + 0.5) * 255;
    const shade = invert ? 255 - gray : gray;
    const o = index * 4;
    out[o] = shade; out[o + 1] = shade; out[o + 2] = shade; out[o + 3] = 255;
  }
  return out;
}

export function defaultWindow(meta: DicomMetadata, values: Float32Array): { center: number; width: number } {
  if (meta.window_center != null && meta.window_width != null && meta.window_width > 0) return { center: meta.window_center, width: meta.window_width };
  const { min, max } = valueRange(values);
  return { center: (min + max) / 2, width: Math.max(1, max - min) };
}
