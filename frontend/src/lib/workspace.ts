import { createContext, useContext } from 'react';
import type { Workspace } from './types';

export const WorkspaceContext = createContext<Workspace | null>(null);

export function useWorkspace() {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) throw new Error('Workspace required');
  return workspace;
}
