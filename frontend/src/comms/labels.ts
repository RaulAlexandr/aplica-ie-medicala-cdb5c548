import type { Channel, ConsentSource, Message, MessageStatus, MessageType } from './api';

export const channelLabel: Record<Channel, string> = { sms: 'SMS', email: 'Email' };
export const typeLabel: Record<MessageType, string> = { confirmation: 'Confirmare programare', reminder: 'Reminder', cancellation: 'Anulare', reschedule: 'Reprogramare' };
export const sourceLabel: Record<ConsentSource, string> = { in_person: 'La recepție', phone: 'Telefon', paper_form: 'Formular pe hârtie', patient_reply: 'Răspuns de la pacient', other: 'Altă sursă' };
export const allowedVariables = ['prenume', 'data', 'ora', 'medic', 'clinica', 'telefon_clinica'];

export function statusLabel(message: Pick<Message, 'status' | 'adapter'>): string {
  const labels: Record<MessageStatus, string> = { pending: 'În așteptare', sent: 'Trimis', delivered: 'Livrat', failed: 'Eșuat', cancelled: 'Anulat' };
  const simulated = message.adapter === 'simulated' && (message.status === 'sent' || message.status === 'delivered');
  return simulated ? `${labels[message.status]} (simulat)` : labels[message.status];
}

const reasons: Record<string, string> = {
  no_consent: 'Fără acord pe acest canal', no_contact: 'Pacientul nu are telefon/email pentru canal', appointment_cancelled: 'Programarea a fost anulată',
  appointment_closed: 'Programarea s-a încheiat', appointment_inactive: 'Programarea nu mai este activă', rescheduled: 'Programarea a fost mutată', superseded: 'Înlocuit de un mesaj mai nou',
  expired: 'Programarea a început deja', attempts_exhausted: 'Încercări epuizate', delivery_failed: 'Livrare eșuată', queued: 'Pus în coadă', revived: 'Reactivat (programare reactivată)',
  quiet_window: 'Amânat: fereastră de liniște',
};
export function reasonLabel(reason: string | null): string {
  if (!reason) return '';
  const code = reason.split(/[ :]/)[0];
  const rest = reason.slice(code.length).trim();
  if (code === 'retry') return `Reîncercare ${rest}`;
  return reasons[code] ? (rest && code === 'attempts_exhausted' ? `${reasons[code]} ${rest}` : reasons[code]) : reason;
}

export function unknownVariables(...texts: string[]): string[] {
  const bad: string[] = [];
  for (const text of texts) {
    for (const match of text.matchAll(/\{([^{}]*)\}/g)) if (!allowedVariables.includes(match[1]) && !bad.includes(match[1])) bad.push(match[1]);
    if (/[{}]/.test(text.replace(/\{[^{}]*\}/g, ''))) bad.push('acoladă nepereche');
  }
  return bad;
}

export const formatInZone = (value: string, timezone: string) => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
