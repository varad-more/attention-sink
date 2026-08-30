/**
 * The configuration and API client, provided once and read everywhere.
 *
 * A context rather than a module singleton so that tests and the Playwright fixtures
 * can supply a different base URL without patching a global.
 */

import { createContext, useContext, useMemo, type ReactNode } from 'react';

import { ApiClient } from './api/client';
import { currentConfig, type AppConfig } from './config';

interface AppServices {
  config: AppConfig;
  api: ApiClient;
}

const ServicesContext = createContext<AppServices | null>(null);

export function AppProvider({ config, children }: { config?: AppConfig; children: ReactNode }) {
  const services = useMemo(() => {
    const resolved = config ?? currentConfig();
    return { config: resolved, api: new ApiClient(resolved) };
  }, [config]);
  return <ServicesContext.Provider value={services}>{children}</ServicesContext.Provider>;
}

function useServices(): AppServices {
  const services = useContext(ServicesContext);
  if (!services) throw new Error('a component used app services outside AppProvider');
  return services;
}

export function useAppConfig(): AppConfig {
  return useServices().config;
}

export function useApi(): ApiClient {
  return useServices().api;
}
