// Cliente GraphQL mínimo: el frontend solo habla GraphQL con el gateway de cada ambiente.

export type Env = "dev" | "test" | "prod";

export const ENDPOINTS: Record<Env, string> = {
  dev: import.meta.env.VITE_GRAPHQL_DEV ?? "http://localhost:8081/graphql",
  test: import.meta.env.VITE_GRAPHQL_TEST ?? "http://localhost:8082/graphql",
  prod: import.meta.env.VITE_GRAPHQL_PROD ?? "http://localhost:8083/graphql",
};

export async function gql<T>(env: Env, query: string, variables: Record<string, unknown> = {}): Promise<T> {
  const response = await fetch(ENDPOINTS[env], {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  if (!response.ok) throw new Error(`El gateway de ${env} respondió ${response.status}`);
  const body = await response.json();
  if (body.errors?.length) throw new Error(body.errors[0].message);
  return body.data as T;
}

export interface Connector {
  connectorId: string;
  powerKw: number;
  connectorType: string | null;
  status: "AVAILABLE" | "RESERVED" | "CHARGING" | "OUT_OF_SERVICE";
  sagaId: string | null;
}
export interface Station {
  stationId: string;
  name: string;
  city: string;
  availableConnectors: number;
  connectors: Connector[];
}
export interface Flag {
  name: string;
  enabled: boolean;
  description: string;
  updatedAt: string | null;
  updatedBy: string | null;
}
export interface SagaEvent {
  step: string;
  action: string;
  status: string;
  at: string;
  detail: string | null;
  code: string | null;
}
export interface Saga {
  sagaId: string;
  status: string;
  fleetId: string;
  connectorId: string;
  energyKwh: number;
  amount: number | null;
  surgeApplied: boolean | null;
  failureReason: string | null;
  createdAt: string;
  compensations: { step: string; action: string; result: string | null; idempotent: boolean; at: string }[];
  events: SagaEvent[];
}
export interface Telemetry {
  powerKw: number;
  voltageV: number;
  temperatureC: number;
  energyDeliveredKwh: number;
  recordedAt: string;
}
export interface Wallet {
  fleetId: string;
  name: string;
  balance: number;
  ledger: { sagaId: string; type: string; amount: number; createdAt: string }[];
}
export interface Dashboard {
  environment: { name: string; version: string; track: string; services: Record<string, string>; tables: Record<string, string> };
  stations: Station[];
  featureFlags: Flag[];
  sagas: Saga[];
}

const SAGA_FIELDS = `sagaId status fleetId connectorId energyKwh amount surgeApplied failureReason createdAt
  compensations { step action result idempotent at }
  events { step action status at detail code }`;

export const DASHBOARD_QUERY = `{
  environment { name version track services tables }
  stations { stationId name city availableConnectors connectors { connectorId powerKw connectorType status sagaId } }
  featureFlags { name enabled description updatedAt updatedBy }
  sagas(limit: 12) { ${SAGA_FIELDS} }
}`;

export const RESERVE_MUTATION = `mutation($input: ReserveChargeInput!) {
  reserveAndActivateCharge(input: $input) { success idempotent message saga { ${SAGA_FIELDS} } }
}`;

export const SET_FLAG_MUTATION = `mutation($name: String!, $enabled: Boolean!) {
  setFeatureFlag(name: $name, enabled: $enabled, actor: "frontend") { name enabled updatedAt updatedBy }
}`;

export const FINISH_MUTATION = `mutation($id: ID!) { finishCharging(connectorId: $id) }`;
export const RETRY_MUTATION = `mutation($id: ID!) { retryCompensation(sagaId: $id) { success message } }`;
export const TELEMETRY_QUERY = `query($id: ID!) { telemetry(connectorId: $id, limit: 12) { powerKw voltageV temperatureC energyDeliveredKwh recordedAt } }`;
export const WALLET_QUERY = `query($id: ID!) { wallet(fleetId: $id) { fleetId name balance ledger { sagaId type amount createdAt } } }`;
