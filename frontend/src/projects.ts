type Project = { id: string; name: string };

export function projectLabel(project: Project, projects: Project[]): string {
  const peers = projects.filter(
    (p) => p.id !== project.id && p.name.trim() === project.name.trim(),
  );
  if (!peers.length) return project.name;
  let length = 8;
  while (
    length < project.id.length &&
    peers.some((p) => p.id.slice(0, length) === project.id.slice(0, length))
  ) {
    length++;
  }
  return `${project.name} · ${project.id.slice(0, length)}`;
}
