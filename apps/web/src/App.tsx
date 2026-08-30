/**
 * The route table.
 *
 * Seven routes, all read-only. There is no route that writes anything, because there
 * is no API endpoint that would accept one: advancing the experiment is a command,
 * not a link.
 */

import { Navigate, Route, Routes } from 'react-router-dom';

import { Layout } from './components/Layout';
import { Echoes } from './routes/Echoes';
import { Graveyard } from './routes/Graveyard';
import { Interviews } from './routes/Interviews';
import { MemoryDetail } from './routes/MemoryDetail';
import { Methodology } from './routes/Methodology';
import { SixMinds } from './routes/SixMinds';
import { Timeline } from './routes/Timeline';

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<SixMinds />} />
        <Route path="cycle/:cycle" element={<SixMinds />} />
        <Route path="graveyard" element={<Graveyard />} />
        <Route path="echoes" element={<Echoes />} />
        <Route path="memory/:memoryId" element={<MemoryDetail />} />
        <Route path="timeline" element={<Timeline />} />
        <Route path="interviews" element={<Interviews />} />
        <Route path="methodology" element={<Methodology />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
