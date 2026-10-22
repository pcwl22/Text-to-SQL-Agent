import { experiments, provenance } from '../data/experiments'
import { demoCases } from '../data/demo'

export interface ObservatoryRepository {
  getExperiments: () => typeof experiments
  getDemoCases: () => typeof demoCases
  getProvenance: () => typeof provenance
}
// Deliberately no HTTP implementation. Future Axios adapters must enforce authorization server-side.
export const offlineRepository: ObservatoryRepository = {
  getExperiments: () => structuredClone(experiments),
  getDemoCases: () => structuredClone(demoCases),
  getProvenance: () => ({ ...provenance }),
}
