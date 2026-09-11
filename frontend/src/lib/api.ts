export class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code = 'request_failed') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

let csrfToken = '';
export const SESSION_EXPIRED_EVENT = 'ordertowork:session-expired';
export function setCsrfToken(token: string | null | undefined) {
  csrfToken = token ?? '';
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set('Content-Type', 'application/json');
  if (csrfToken && !['GET', 'HEAD'].includes((options.method ?? 'GET').toUpperCase())) {
    headers.set('X-CSRF-Token', csrfToken);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...options, credentials: 'include', headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(
      'We could not reach OrderToWork. Check your connection and try again.',
      0,
      'network_error',
    );
  }
  const contentType = response.headers.get('content-type') ?? '';
  const data =
    response.status === 204
      ? null
      : contentType.includes('application/json')
        ? await response.json()
        : await response.text();
  if (!response.ok) {
    if (
      response.status === 401 &&
      !path.startsWith('/auth/') &&
      !path.startsWith('/customer/') &&
      !path.startsWith('/handover/')
    ) {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    const detail = typeof data === 'object' && data ? (data.detail ?? data) : data;
    let message = 'This request could not be completed. Please try again.';
    let code = `http_${response.status}`;
    if (typeof detail === 'string' && detail.length < 1000) message = detail;
    else if (Array.isArray(detail))
      message = detail
        .map(
          (item: { msg?: string; loc?: string[] }) =>
            `${item.loc?.slice(1).join(' ') || 'Field'}: ${item.msg ?? 'invalid value'}`,
        )
        .join('. ');
    else if (detail && typeof detail === 'object') {
      message = detail.message ?? detail.error ?? message;
      code = detail.code ?? code;
    }
    if (code === 'demo_expired') window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    throw new ApiError(message, response.status, code);
  }
  return data as T;
}

export const post = <T>(path: string, data: unknown = {}) =>
  api<T>(path, { method: 'POST', body: JSON.stringify(data) });
export const patch = <T>(path: string, data: unknown) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(data) });
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' });
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'Something went wrong. Please try again.';
export const newIdempotencyKey = () => crypto.randomUUID();
