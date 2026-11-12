# OpsLens 운영 스크립트

폐쇄망 배포/운영에 쓰이는 스크립트 모음. Docker 이미지 export/import, DB 백업/복원.

| 파일 | 역할 |
|------|------|
| `export-images.sh` / `export-images.ps1` | 개발망에서 Docker 이미지 tar로 내보내기 |
| `import-and-run.sh` | 폐쇄망에서 이미지 로드 + 컨테이너 기동 |
| `update-images.sh` | 폐쇄망 버전 업데이트 |
| `backup-db.sh` | DB 백업 |
| `restore-db.sh` | DB 복원 |
| `rehearsal/` | 시나리오 리허설 — 반입·PoC·큰 머지 전 사본에서 시연 흐름 통과/실패(README 참고) |

자세한 배포 절차는 `docs/operations/deployment-closed-network.md` 참고.
