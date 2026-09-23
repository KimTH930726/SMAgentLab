import { create } from 'zustand';
import { queryClient } from '../App';
import { runTrack2, type Track2Result } from '../api/policy';

/**
 * 골든셋 비교 실행("저장소 전략 실험실", PolicyLab.tsx) 상태를 컴포넌트 밖으로 뺀다
 * (2026-09-23) — "비교 실행했다가 다른 화면 갔다가 돌아오면 초기화돼 있다"는 지적.
 *
 * 원인: 비교 실행은 89문항 재임베딩 때문에 몇 분씩 걸리는데, 이전엔 `useMutation`과
 * 그 결과(`lastResult`)가 PolicyLab 컴포넌트의 로컬 state였다. 관리자 탭을 옮기면
 * PolicyLab이 언마운트되고 — 브라우저의 실제 fetch 요청 자체는 계속 진행되고 백엔드도
 * 끝까지 계산해서 DB에 저장하지만(`track2.save_run()`, 응답 여부와 무관하게 먼저 실행됨)
 * — 응답이 도착했을 때 그걸 받을 컴포넌트가 이미 사라진 상태라 결과가 조용히 버려졌다.
 * 돌아오면 "이번 세션엔 아직 재실행 안 함" 폴백 배너와 함께 그 이전 결과만 보임 — 사용자
 * 입장에선 "초기화"로 보임.
 *
 * chat 스트리밍이 이미 같은 문제를 겪었고(useStreamStore.ts) 같은 해법을 씀 — 상태를
 * 컴포넌트가 아니라 모듈 레벨(zustand)에 둬서 화면 이동에 안 묶이게 한다. 여기선 한 걸음
 * 더: 완료 시 `queryClient.invalidateQueries(['track2-history-lab'])`로 실제 DB에 이미
 * 저장된 이력을 다시 불러오게 해서(재구성 없이) 결과를 화면이 안 떠 있어도 확실히 반영한다.
 */
interface Track2State {
  running: boolean;
  axis: string | null;
  error: string | null;
  lastResult: Track2Result | null;
}

export const useTrack2Store = create<Track2State>(() => ({
  running: false,
  axis: null,
  error: null,
  lastResult: null,
}));

export async function runTrack2Comparison(topK: number, axis: string) {
  useTrack2Store.setState({ running: true, axis, error: null });
  try {
    const result = await runTrack2(topK, axis);
    useTrack2Store.setState({ running: false, lastResult: result });
    // 서버가 이미 저장한 실행 이력을 다시 불러온다 — 화면이 그 사이 안 떠 있었어도(언마운트)
    // 이 호출은 store 함수 안이라 컴포넌트 생명주기와 무관하게 실행된다.
    queryClient.invalidateQueries({ queryKey: ['track2-history-lab'] });
  } catch (e) {
    useTrack2Store.setState({ running: false, error: e instanceof Error ? e.message : String(e) });
  }
}
