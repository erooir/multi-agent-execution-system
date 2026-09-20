import { createContext, useContext, useState, type ReactNode } from "react";
const ProjectContext = createContext<[string, (id: string) => void]>([
  "",
  () => {},
]);
export function ProjectProvider({ children }: { children: ReactNode }) {
  const [project, setProject] = useState(
    () => sessionStorage.getItem("research-project") || "",
  );
  function selectProject(id: string) {
    setProject(id);
    sessionStorage.setItem("research-project", id);
  }
  return (
    <ProjectContext.Provider value={[project, selectProject]}>
      {children}
    </ProjectContext.Provider>
  );
}
export function useProject() {
  return useContext(ProjectContext);
}
