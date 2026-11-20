// T03 allowed locations of a catalog service. Mirrors backend app/procedures/locations.py;
// the server stays authoritative (422), the forms only avoid offering forbidden locations.
export type AllowedLocation = 'any' | 'no_tooth' | 'tooth' | 'tooth_surface';

export const ALLOWED_LOCATION_OPTIONS: ReadonlyArray<{ value: AllowedLocation; label: string }> = [
  { value: 'any', label: 'Oricare — dinte și suprafețe opționale' },
  { value: 'no_tooth', label: 'Fără dinte — ex. consultație, igienizare' },
  { value: 'tooth', label: 'Dinte obligatoriu — suprafețe opționale' },
  { value: 'tooth_surface', label: 'Dinte și cel puțin o suprafață' },
];

const SHORT_LABELS: Record<AllowedLocation, string> = {
  any: 'localizare liberă', no_tooth: 'fără dinte', tooth: 'dinte obligatoriu', tooth_surface: 'dinte + suprafață',
};

export function ruleOf(service?: { allowed_location?: string | null } | null): AllowedLocation {
  const value = service?.allowed_location;
  return value === 'no_tooth' || value === 'tooth' || value === 'tooth_surface' ? value : 'any';
}

export const locationLabel = (rule: AllowedLocation) => SHORT_LABELS[rule];
export const allowsTooth = (rule: AllowedLocation) => rule !== 'no_tooth';
export const requiresTooth = (rule: AllowedLocation) => rule === 'tooth' || rule === 'tooth_surface';

/** Message shown while the chosen location does not satisfy the service rule; null when it does. */
export function locationProblem(rule: AllowedLocation, tooth: string, surfaceCount: number): string | null {
  if (rule === 'no_tooth' && (tooth || surfaceCount > 0)) return 'Serviciul se înregistrează fără dinte; elimină dintele și suprafețele.';
  if (rule === 'tooth' && !tooth) return 'Serviciul cere un dinte FDI.';
  if (rule === 'tooth_surface' && (!tooth || surfaceCount === 0)) return 'Serviciul cere un dinte FDI și cel puțin o suprafață.';
  return null;
}
