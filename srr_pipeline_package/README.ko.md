# GENoAR 파이프라인 패키지 (Dockerized)

`srr_pipeline/README.md`를 기반으로 SRR 파이프라인 1–9단계를 도커로 통합했습니다. SLURM 없이 로컬 Docker 환경에서 동일한 단계를 재현 가능하게 실행합니다.

구현된 단계 (1–9)
- 1단계: `.sra` → SRR별 디렉토리 정리
- 2단계: `fastq-dump --split-files` 병렬 실행(GNU Parallel)
- 3단계: `.fastq` → `.fastq.gz` 병렬 압축(pigz/gzip)
- 4단계: `.fastq.gz` 존재 여부로 성공/실패 분류
- 5단계: 성공 샘플 `/work/results/success`로 이동
- 6단계: 샘플 FASTQ 개수별 TSV 생성(1–4개) 및 Cell Ranger에 도달할 수 없는 샘플 기록
- 7단계: `<sample>_S1_R1_001.fastq.gz`, `_R2_001.fastq.gz`로 리네임. 안전하게 바꿀 수 없는 배치는 거부
- 8단계: Snakemake 로컬 모드로 Cell Ranger 실행
- 9단계: 실패 샘플(R1/R2 스왑, `retry_failed/`로 이동, 리스트 작성)

## Cell Ranger 처리 자격

Cell Ranger는 R1/R2 쌍을 요구합니다. FASTQ가 1개뿐인 샘플은 1–5단계가 아무리 잘
끝났어도 처리할 수 없고, 8단계는 그 샘플에 대해 `cellranger count` 잡을 돌리지
않습니다. 이 사실이 깨끗한 종료 코드 뒤로 사라지지 않도록 파이프라인이 명세를
남깁니다.

- **6단계**는 샘플별 FASTQ 개수를 세어, 자격 없는 샘플을 사유와 함께
  `success/cellranger_ineligible.tsv`에 기록합니다(`no fastq.gz files found`,
  또는 `only 1 FASTQ file; Cell Ranger requires paired R1/R2 reads`).
- **7단계**는 `fastq-dump` 원본 출력을 리네임합니다. 디스크에 있는 것이 **완전한**
  배치일 때만 건드리지 않습니다. 같은 샘플의 R1과 R2가 모두 있어야 하고, 단일 레인
  (`<sample>_S1_R[12]_001.fastq.gz`) 또는 멀티 레인
  (`<sample>_S*_L*_R[12]_*.fastq.gz`, 모든 레인의 R1이 같은 레인의 R2와 짝을 이룸)
  이어야 합니다. R2 없는 R1, R1 없는 R2, 레인이 서로 어긋난 멀티 레인 세트는 모두
  **거부**하고 로그에 남긴 뒤 같은 TSV에 추가합니다. `_R1_`/`_R2_`/`_I1_`/`_I2_`
  토큰은 있는데 이 스크립트가 아는 쌍을 이루지 못하는 경우도 마찬가지입니다. 그런
  샘플을 절반만 리네임하면 실제 배치를 알 수 있는 유일한 증거가 사라지고, 원본
  출력에나 맞는 크기 기반 휴리스틱이 엉뚱한 두 파일을 조용히 집어 들기 때문입니다.
- **8단계**는 같은 검사를 다시 적용하고, Snakemake 시작 전에 전체 명세를
  `success/cellranger_eligibility.tsv`에 기록합니다(샘플당 한 줄, 자격 여부와 사유).
  첫 줄은 `# run_id <id>`로, 이 선택을 한 실행의 id입니다. 앞 단계들이 볼 수 없는
  제외 사유도 `success/cellranger_ineligible.tsv`에 덧붙입니다. FASTQ가 4개를 넘는
  샘플은 6단계 목록에 오르지도 않고 7단계가 처리하지도 않으므로, 그런 샘플의 불완전한
  레인 구성은 여기서만 드러납니다.

7단계와 8단계는 FASTQ 이름을 같은 방식으로 읽으며, 어떤 배치를 Cell Ranger에 넘길 수
있는지에 대해 같은 판단을 합니다. 자격이란 **완전한** 읽기 세트를 뜻합니다. 단일 레인
쌍이거나, R1을 가진 모든 레인이 그 레인의 R2도 가진 멀티 레인 세트여야 합니다.
Cell Ranger는 샘플 디렉터리를 통째로 받아
(`--fastqs <dir> --sample <name>`) 스스로 다시 글롭하므로, 짝이 없는 레인을 여기서
파일을 골라 감출 수는 없습니다.

업그레이드하는 경우를 위해 밝혀 둡니다. 8단계는 예전에 `_L*_R1_` 파일이 하나라도
있고 `_L*_R2_` 파일이 하나라도 있는지만 물었습니다. R1은 한 레인, R2는 다른 레인에
있는 샘플도 이 조건을 만족해 선택됐고, `cellranger count`가 그 샘플에서 실패했습니다.
그 실패 하나가 Snakemake 단계 전체를 실패시켰으므로 실행은 거기서 끝났고, 아직
도달하지 못한 멀쩡한 샘플들도 분석되지 못했습니다. 그렇게 실패하던 실행이 이제는
완주합니다. 해당 샘플은 Snakemake가 시작하기 전에
`multi-lane FASTQ names do not pair R1 with R2 within the same lane` 사유로
제외됩니다.

## 이 실행이 "했다"고 주장하는 것

`/work/results`는 영속 볼륨이므로 "디스크에 BAM이 있다"는 사실만으로는 어느 실행이
만든 것인지 알 수 없습니다. 그래서 완료의 정의는 이렇습니다. **이번 실행이 처리하기로
한 모든 샘플이, 이번 실행의 입력과 출처가 일치하는 Cell Ranger 산출물을 가지고 있을
것.**

각 실행은 1단계가 파일을 옮기기 전에 그 기대 집합을 고정하고, 계획과 결과를
`results/runs/<run_id>/` 아래에 남깁니다.

| 경로 | 내용 |
|------|------|
| `results/runs/LATEST` | 가장 최근 실행의 id |
| `results/runs/<run_id>/expected_samples.tsv` | 입력 샘플당 한 줄, 내용 지문 포함. 1단계 전에 고정 |
| `results/runs/<run_id>/cellranger/<sample>.json` | 완료 기록. Cell Ranger 규칙이 자기가 실제로 돌린 샘플마다, 그 결과로 나온 파일을 적어 남깁니다 |
| `results/runs/<run_id>/outcome.json` | 샘플별 상태와 집계, 그리고 다른 실행에 속한 샘플 목록 |
| `results/runs/<run_id>/samples/<sample>.json` | 같은 기록을 샘플별 파일로 |
| `results/runs/<run_id>/outcome.env`, `reasons.txt` | 최종 배너가 읽는 값 |
| `results/success/<sample>/.genoar_cellranger.json` | 영수증. 이 BAM을 그 입력과 그것을 만든 실행에 묶습니다. `fresh`와 `adopted`에 대해서만 기록 |

위 경로는 모두 실행이 스스로 만듭니다. 근거는 한 방향으로만 흐릅니다. Cell Ranger
규칙이 완료 기록을 쓰고, 오케스트레이터가 그것으로부터 영수증을 만들며, 실행보다
오래 남는 것은 영수증뿐입니다.

run id는 `GENOAR_RUN_ID`, 없으면 설정의 `run_id`, 그것도 없으면 자동 생성입니다.
`results/runs/` 아래 디렉터리 이름이 되므로 크롤러와 같은 규칙을 적용합니다.
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`이며, 앞뒤 공백은 잘라내고 `LATEST`는 가장 최근
실행을 가리키는 포인터용으로 예약돼 있습니다(비교가 대소문자를 구분하므로 `latest`는
써도 됩니다). 규칙을 어기거나 이미 존재하는 실행 디렉터리를 가리키는 id는 구성
오류입니다. 어떤 단계도 시작하기 전에 `2`로 종료합니다. 실행 기록은 덮어쓰지 않습니다.
*자동 생성된* id가 충돌하면 그때는 다시 생성합니다. 사람이 고른 id가 아니기
때문입니다. 재개는 *같은* 기대 집합에 대한 *새로운* 실행입니다. 새 id를 받고, 앞선
산출물이 그대로 있는 것을 확인해 그 샘플들을 cache hit로 보고합니다.

**id 선점은 원자적입니다.** 실행은 `results/runs/<id>/`를 단일 `mkdir` 호출로
확보합니다. 커널이 이 호출을 직렬화하므로 정확히 하나만 성공합니다. 같은 id로 두
실행을 같은 순간에 시작하면 하나가 진행하고, 다른 하나는 `2`로 종료합니다. 디렉터리
존재를 먼저 확인하고 나중에 만들면 그 사이에 다른 실행이 같은 id를 확보해, 두 실행이
한 디렉터리에 각자의 manifest와 기록을 쓰게 됩니다.

실행 디렉터리를 아예 만들지 못한 경우는 `1`로 종료합니다. 배너는 이것이 id 선점을
뜻하지 **않는다**고 밝힙니다.

기대 샘플은 반드시 다음 중 하나로 끝납니다.

| 상태 | 완료로 셈? | 의미 |
|------|:---:|------|
| `fresh` | 예 | 이번 실행의 Cell Ranger 작업이 산출물을 만들었고, 그렇다는 완료 기록을 남김 |
| `cache_hit` | 예 | 검증된 작업을 기록한 영수증(`"provenance": "fresh"`)이 이 입력에 대해 바로 그 파일을 보증함. 진짜 재개이며 새로 한 일로는 보고하지 않음 |
| `adopted` | **아니오** | 요청에 따라 기존 산출물을 인정(`GENOAR_ADOPT_PRIOR_RESULTS=1`). 이번 실행의 입력과 묶어 주는 근거가 없음. 기록만 하고 완료로는 세지 않음. 영수증에 채택이 적히므로 이후의 모든 실행도 다시 `adopted`로 보고하며 `cache_hit`이 되지 않음. 채택을 끝내는 것은 그 산출물을 새로 만드는 Cell Ranger 실행뿐임 |
| `unverified` | **아니오** | 산출물의 출처를 보증하는 것이 없음 |
| `stale` | **아니오** | 영수증은 있으나 다른 입력을 가리킴 |
| `ineligible` | 아니오 | Cell Ranger 이전에 걸러짐. 사유가 기록됨 |
| `missing` | 아니오 | 산출물 없음 |

채택은 재사용한 산출물을 기록합니다. 검증된 완료에 포함되지 않으며 exit 0 판정에서
제외됩니다. 배너는 두 집계를 별도의 줄로 출력합니다.

```text
[result]   verified complete: 2 (fresh 1, cache hit 1)
[result]   adopted, NOT verified and NOT counted as complete: 1
```

검증됐다는 것은 그 산출물이 이 입력의 것임을 뒷받침하는 근거를 실행이 지목할 수
있다는 뜻입니다. 근거는 이번 실행 자신의 Cell Ranger 완료 기록이거나, 그 작업을 한
실행이 남긴 영수증입니다. 채택은 실행이 확인할 수 없는 산출물을 쓰라고 운영자가
지시했다는 뜻입니다. 완료라고 할 것이 채택뿐인 실행에는 검증된 샘플이 하나도
없습니다. 그런 실행은 `4`로 종료합니다. `3`은 재사용한 것이 없는 실행을 보고하는
코드이며, 이 실행은 산출물을 재사용했습니다.

수정 시각은 출처가 아닙니다. 어떤 입력이 그 파일을 만들었는지 말해 주지 못하고,
실행이 시작된 바로 그 초에 쓰인 남의 BAM도 이 조건을 만족합니다. "샘플이 자격을
갖췄고 Snakemake가 0으로 끝났다"도 근거가 아닙니다. 자격은 어떤 작업보다 먼저
판정되고, Snakemake는 할 일이 하나도 남지 않았을 때 바로 0으로 끝나기 때문입니다.
그래서 이 주장은 추론하지 않습니다. Cell Ranger를 돌리는 규칙이 자기가 돌린 샘플마다
완료 기록을 남기고, 근거는 다음 순서로 따집니다.

1. **이번 실행의 완료 기록이 있고, 그 기록이 디스크에 있는 파일을 여전히 설명하는
   경우.** 규칙이 이번 실행에서 여기서 Cell Ranger를 돌렸고, 나온 것이 이 파일입니다.
   `fresh`. 이번 실행 자신의 직접 관찰이므로 가장 먼저 따집니다. 아래 항목들은
   앞선 실행에 대한 추론입니다.
2. **바로 그 파일을 이미 보증하면서, 검증된 작업이 만들었다고 밝히는 영수증.**
   같은 입력 지문, 같은 BAM, 그리고 영수증의 `"provenance": "fresh"`. 이 정확한
   입력에 대해 앞선 실행이 한 일이 그대로 남아 있는 것입니다. `cache_hit`이며,
   재사용이고 새로 한 일로 주장하지 않습니다. 채택된 산출물이라고 밝히는 영수증은
   다시 `adopted`입니다. 둘 중 어느 것도 밝히지 않는 영수증은 `unverified`입니다.
   재사용한 작업과 남의 말만 믿고 받아들인 산출물을 가릴 수 없기 때문입니다.
3. **반증하는 영수증.** 다른 입력으로 만든 산출물(`stale`), 또는 이 입력으로
   만들었지만 지금 디스크에 있는 파일은 아닌 경우(`unverified`. 그 뒤로 교체되거나
   touch된 것이며, 지금 있는 것은 이번 실행이 만든 것이 아닙니다).
4. **명시적으로 요청한 채택.** `adopted`로 기록하고 별도의 줄로 보고하며, 어느
   집계에도 넣지 않습니다.
5. 그 외에는 근거가 없습니다. 근거가 없다는 것이 유리하게 작용하지는 않습니다.
   `unverified`이며 완료로 세지 않습니다.

**"디스크에 있는 파일을 여전히 설명한다"**는 것은 산출물이 차지한 파일과 그 파일 양
끝 64 KiB를 뜻합니다. 완료 기록에는 BAM이 놓인 device와 inode, 크기, 시각 하한, 앞뒤
64 KiB의 SHA-256을 적습니다. 해싱하는 것은 양 끝뿐입니다. Cell Ranger BAM은 수 GB이며,
매 실행마다 전체를 해싱하면 모든 정상 실행이 그 비용을 부담하게 됩니다.

판정은 inode가 합니다. 다이제스트가 판정하는 경우는 하나입니다. inode 번호를 제외한
나머지가 모두 일치하면 실행은 기록된 다이제스트를 비교합니다. 다이제스트는 파일의
바이트에서 계산하므로 파일시스템의 장부에 의존하지 않습니다. 일부 파일시스템은
바뀌지 않은 파일의 inode 번호를 그대로 두지 않습니다. macOS Docker Desktop bind
mount가 그렇습니다. 그런 환경에서는 다이제스트가 산출물을 식별하며, bind mount된
results 디렉터리 위에서도 정상 실행은 정상적으로 완료하고 `0`으로 종료합니다.

규칙은 파일 양 끝을 읽지 못하면 그 자리에서 경고하며, 그 기록에는 다이제스트가
없습니다. 파일 번호를 다시 매기는 파일시스템에서는 판정을 지어 줄 것이 남지
않습니다. 해당 샘플은 `unverified`로 보고합니다. 사유에는 그 상태를 만드는 두 경우를
모두 적습니다. 파일 번호를 다시 매기는 파일시스템인 경우와, 산출물이 같은 크기·같은
시각의 다른 파일로 바뀐 경우입니다. 각각의 조치와, 실행이 둘 중 어느 쪽인지 판별할 수
없다는 사실도 함께 적습니다. Cell Ranger는 Linux-x86-64 전용이므로 Mac은 Stage 3의
예행 환경입니다.

따라서 Cell Ranger 산출물은 있는데 완료 기록도 영수증도 그것을 설명하지 못하는
샘플은 `unverified`이고 완료로 세지 않습니다. 이 장치가 생기기 전에 만들어진
산출물도 마찬가지입니다. 도중에 죽은 작업은 기록 옆에 `<sample>.started` 표시만
남기는데, 이것으로는 아무것도 인정되지 않습니다. 일을 시작했다는 말이며, 끝냈음을
보이지는 못하기 때문입니다.

영수증은 실행이 산출물을 보증할 수 있는 경우에만 기록합니다. 이번 실행이 만든
것(`fresh`)과 운영자가 기록을 남기고 채택한 것(`adopted`)입니다.

이번 실행의 기대 샘플이 아닌 산출물은 `other_samples_with_output`에 나열하고 그대로
두며, 이번 실행의 성과로 세지 않습니다.

> **업그레이드 시 주의.** 입력 지문이 이제 여러 파일로 이뤄진 FASTQ 입력의 **모든**
> 파일을 포함합니다. 예전에는 가장 큰 파일 하나만 봤습니다. 따라서 이전 버전이 그런
> 샘플에 대해 남긴 영수증은 더 이상 일치하지 않고, 업그레이드 후 첫 실행에서 그
> 샘플들은 `unverified`로 읽힙니다. 다시 실행하거나
> `GENOAR_ADOPT_PRIOR_RESULTS=1`로 기존 산출물을 인정하십시오. 파일이 하나인 `.sra`
> 입력의 지문은 그대로이므로 영향이 없습니다.

## 보고와 종료 코드

Snakemake는 처리할 샘플 목록이 비어 있어도 0으로 종료합니다. 따라서 "8단계가 0으로
끝났다"는 것은 Cell Ranger가 돌았다는 뜻이 아닙니다. 파이프라인은 위의 기대 집합을
기준으로 판정하며, 종료 코드도 그 판정을 따릅니다.

| 종료 코드 | 최종 배너 |
|-----------|-----------|
| `0` | 기대 샘플 전부가 검증된 Cell Ranger 산출물을 가짐. 이 이미지에 Cell Ranger 단계 자체가 없는 경우(1–7단계 빌드 대상)도 이 코드입니다. 후자는 명시적으로 밝힙니다 |
| `1` | 어느 단계가 실패했는지 배너가 지목합니다. 실행 디렉터리 자체를 만들지 못한 경우도 포함합니다(읽기 전용 트리, 디스크 가득 참, 마운트 사라짐) |
| `2` | 구성 오류. 쓸 수 있는 `cellranger` 실행 파일이 없거나, `cellranger --version`이 버전을 내놓지 못했거나, QC 하한선(`min_cells`, `min_valid_barcodes`, `min_transcriptome`)이 숫자가 아니거나, `/ref`가 쓸 수 있는 reference가 아니거나, run id가 쓸 수 없거나 예약어이거나 이미 존재하는 실행을 가리키거나 같은 순간에 다른 실행이 선점함. 아무것도 실행하기 전에 판정 |
| `3` | 유효하게 완주했고 분석된 기대 샘플이 0건. 입력 자체가 없었던 실행도 이 코드로 보고 |
| `4` | 부분 완료. 기대 샘플 전부에 대해 검증된 산출물을 보이지는 못하고, 이번 실행이 설명할 수 있는 산출물을 가진 기대 샘플이 있음. 그 산출물은 검증된 것(일부만 완료)이거나 채택된 것입니다. 사유를 원인별로 묶어 출력 |

구성 오류를 `3`으로 두지 않은 것은 의도적입니다. "할 일이 없다"와 "아무것도 할 수
없다"는 다른 대답이고, 고쳐야 하는 쪽은 후자뿐입니다.

`1`과 `2`를 갈라 둔 이유도 같습니다. `2`는 사용자가 고른 id에 대한 것입니다. `1`은
볼륨에 대한 것입니다. 디스크가 가득 찬 상황을 run id 선점으로 보고하면, 문제가 없던
것을 고치러 가게 됩니다.

> `4`는 여기서 "부분 완료"를 뜻합니다. 병렬 크롤 스크립트의 `4`는 전혀 다른 의미
> (워커는 정상 종료했는데 *그 실행이* 수집한 파일이 0건)입니다. `0`–`3`은
> 프로젝트 전체에서 동일합니다. 진입점별 표는 루트 README의
> [실행 결과 읽기](../README.ko.md#실행-결과-읽기)에 있습니다.

### 아무것도 실행하기 전에 확인하는 준비물

step 0은 다음 두 가지가 모두 성립하지 않으면 시작을 거부합니다. 발견한 문제를 모두
나열하며, 처음 하나에서 멈추지 않습니다.

- `/opt/cellranger/cellranger` 또는 `/opt/cellranger/bin/cellranger`(호스트의
  `./cellranger/` 설치)가 존재하고 실행 가능할 것. Cell Ranger는 실행 파일 주변의
  파일도 사용하므로 설치 루트 전체를 마운트합니다.
- `/ref`(호스트의 `./ref/`) 최상위에 비어 있지 않은 `reference.json`과 `fasta/`,
  `genes/`, `star/` 디렉터리가 있을 것. 압축을 풀지 않았거나 `refdata-gex-*/` 한
  단계 아래에 들어 있는 reference는 조건을 만족하지 않습니다.

`make run-stage3`은 컨테이너를 띄우기 전에 호스트에서 같은 두 검사를 수행합니다.
한쪽이 받아들인 설치는 다른 쪽도 받아들입니다.

### 어느 파이프라인을 실행하는가

공개 기본값은 유지보수 중인 현재/수정 트리(`legacy_pipeline: false`)입니다.
클러스터에서 산출물을 확인한 이전 트리는 비교 기준으로 동결되어 있으며,
별도 결과 디렉터리에서 `legacy_pipeline: true`로 명시해 재현할 수 있습니다.
기존 `pipeline_mode: verified|corrected`와 `safe_mode: true|false` 설정도 계속
동작합니다.

## 폴더 구조

```
srr_pipeline_package/
├─ docker/
│  └─ Dockerfile                  # 멀티스테이지; 최종 대상: step9
├─ pipeline_entry.sh              # 설정에 따라 현재/동결 트리를 선택
├─ pipeline/                       # 동결된 과거 트리(legacy_pipeline: true)
│  ├─ run_docker_pipeline.sh      # 1–9단계 오케스트레이션
│  ├─ lib.sh                      # 로깅/리포트 헬퍼
│  ├─ make_directories.sh         # Step 1
│  ├─ fastq_dump_parallel.sh      # Step 2
│  ├─ fastq_gzip_parallel.sh      # Step 3
│  ├─ parse_fastq_logs.py         # Step 4
│  ├─ move_success_dirs.sh        # Step 5
│  ├─ check_counts.sh             # Step 6
│  ├─ rename_fastq.sh             # Step 7
│  ├─ Snakefile_hs.smk            # Step 8
│  └─ rename_move_failed_fastqs.py# Step 9
├─ pipeline_next/                  # 유지보수 중인 현재/수정 트리(공개 기본값)
├─ requirements.base.txt          # PyYAML 버전 고정
├─ requirements.snakemake.txt     # Snakemake 버전 고정
├─ configs/
│  └─ example.yaml                # 예시 설정(경로, 코어 등)
└─ docs/
   ├─ README.md                   # 영문 개요(원본)
   ├─ USER_GUIDE.md               # 사용자 가이드
   └─ TROUBLESHOOTING.md          # 트러블슈팅 가이드
```

## 빠른 시작(Quick Start)

프로젝트 루트에서 `make build-srr`와 Stage 3 Make 타깃들이 아래 두 명령을 감싸며,
실행 전에 준비물을 먼저 확인합니다. `make fetch-sra`는 Stage 2 품질 필터를 통과한
액세션을 `sample_sra/`에 캐시하고 `make run-fetched-stage3`용 정확한 입력 뷰를
만듭니다. 직접 실행하는 `make run-stage3`은 `sample_sra/`의 사용자 관리 입력을
처리합니다. 아래는 이미지를 직접 다루는 경우의 원형 명령입니다.

1) 데이터/설정 준비
- 호스트 `./sample_sra/SRRxxxxxx.sra`
- 호스트 `./ref` (Cell Ranger reference)
- 호스트 `./cellranger/` 설치 (`cellranger` 또는 `bin/cellranger`가 실행 가능)
- `srr_pipeline_package/configs/example.yaml` 값 확인/수정

2) 이미지 빌드
- `docker build -f srr_pipeline_package/docker/Dockerfile --target step9 -t genoar-srr:step9 .`

3) 실행(1–9단계)
- 필수 마운트
  - `-v $(pwd)/sample_sra:/work/data/sra`
  - `-v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml`
  - `-v $(pwd)/ref:/ref`
  - `-v $(pwd)/cellranger:/opt/cellranger`
- 선택 마운트
  - `-v $(pwd)/logs:/work/logs`
  - `-v $(pwd)/results:/work/results`
- 실행 명령
  - `docker run --rm -it -v $(pwd)/sample_sra:/work/data/sra -v $(pwd)/srr_pipeline_package/configs/example.yaml:/work/config.yaml -v $(pwd)/ref:/ref -v $(pwd)/cellranger:/opt/cellranger -v $(pwd)/logs:/work/logs -v $(pwd)/results:/work/results genoar-srr:step9`

## 재현성
- Python 패키지 고정: `pyyaml==6.0.2`, `snakemake==7.32.4`
- 시스템 패키지: `sra-toolkit`, `parallel`, `gzip`, `pigz`
- 베이스 이미지 고정: `python:3.11.9-slim`

## 산출물/진단
- 결과(`/work/results`):
  - `runs/<run_id>/`(이번 실행의 기대 샘플 명세와 결과.
    [이 실행이 "했다"고 주장하는 것](#이-실행이-했다고-주장하는-것) 참고), `runs/LATEST`
  - `success/SRRxxxxxx/`(리네임된 FASTQ, 성공 시 `cellranger_output/outs/...`와
    영수증 `.genoar_cellranger.json`)
  - `retry_failed/SRRxxxxxx/`(9단계 후 재시도 준비)
  - `fastq_success.txt`, `fastq_failed.txt`
  - `success/fastq_[1-4]_files.tsv`
  - `success/cellranger_ineligible.tsv` (6단계 명세, 7단계가 추가 기록)
  - `success/cellranger_eligibility.tsv` (8단계의 전체 명세, 모든 샘플)
- 로그(`/work/logs`): `stepN_*.log`
- 리포트(`/work/results/reports`):
  - `stepN.ok` — 제 할 일을 마친 단계
  - `stepN.err` — 실패한 단계
  - `stepN.warn` — 완주했으나 할 일을 하지 못한 단계(자격 샘플 0건인 8단계).
    `stepN.ok` **대신** 기록되므로 `.ok`를 찾는 도구는 아무것도 찾지 못합니다
  - `stepN.msg` (`.warn`·`.err` 옆의 사유)
  - `stepN_failed.txt` (해당 단계 실패 항목)
  - `summary.jsonl` (스텝 상태 요약)
- 자세한 오류 유형/대응: `docs/TROUBLESHOOTING.md`

## 설계/변경 요약 (기존 파이프라인 대비)
- 오케스트레이션: 다단계 스크립트/SLURM → 단일 엔트리포인트 + config.yaml
- 병렬화: SLURM 배열 → GNU Parallel(2–3단계), Snakemake 로컬(8단계)
- 로깅/판별: SLURM 로그 파싱 → 실행별 provenance/outcome 검증 + 표준 로그/리포트
- 패키징: 호스트 의존 → 버전 고정 Docker 이미지
- Cell Ranger: 이미지 미포함(라이선스) → 호스트 마운트(/opt/cellranger), `/ref` 마운트

## 기술 구현 개요
- Dockerfile(멀티스테이지): step1…step9로 점진 탑재하며, `step9`에는 유지보수 중인 `/pipeline_next`와 동결 비교용 `/pipeline`을 함께 포함
- `/pipeline_entry.sh`: 기본적으로 `/pipeline_next`를 선택하며, 동결 트리는 `legacy_pipeline: true`를 명시한 경우에만 선택
- `/pipeline_next/run_docker_pipeline.sh`: 설정 로딩 → 1–9단계 순차 실행, 단계 및 실행 결과 기록
- 스텝 스크립트: GNU Parallel/pigz 사용, 실패 항목을 `reports/stepN_failed.txt`에 누적

## 마이그레이션 치트시트
- `make_directories.sh` → `/pipeline_next/make_directories.sh`
- `slurm_fastq_submit.sh`→`fastq_dump_job.sh`→`/pipeline_next/fastq_dump_parallel.sh`
- `slurm_gzip_submit.sh`→`fastq_gz_job.sh`→`/pipeline_next/fastq_gzip_parallel.sh`
- `parse_fastq_logs.py` → `/pipeline_next/parse_fastq_logs.py`
- `move_success_dirs.sh` → `/pipeline_next/move_success_dirs.sh`
- `check_counts.sh` → `/pipeline_next/check_counts.sh`
- `rename_fastq.sh` → `/pipeline_next/rename_fastq.sh`
- `run_cellranger_hs.sh`/`Snakefile_hs.smk` → `/pipeline_next/Snakefile_hs.smk`(로컬)
- `rename_move_failed_fastqs.py` → `/pipeline_next/rename_move_failed_fastqs.py`

## 한계/다음 단계
- 대규모 성능과 용량은 각 사이트의 스케줄러·할당량·스토리지 정책에 맞춰 추가 검증 필요
- 생성형 HPC 인계는 fail-closed이지만, 지원하는 모든 스케줄러/사이트 조합에서 실증된 것은 아님
- 부분 실행 플래그: `--from-step/--to-step` 지원 검토
