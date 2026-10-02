# Drum Practice: documento do projeto

Notas de projeto em português: o que o app faz, como foi construído, cada decisão e as medições
que a justificaram (inclusive o que foi testado e descartado). Para instalar e usar, veja o
[README](../README.md).

O app transforma uma música (link ou arquivo) numa aula de bateria: separa a bateria, transcreve
as batidas, acha padrões e passos, e oferece um player com kit, grade, modo Steps e um "Guitar
Hero" para bateria eletrônica via MIDI. O código também transcreve outros instrumentos, mas essa
parte está congelada e escondida (`MUSIC_PRACTICE_ALL_INSTRUMENTS=1` mostra).

Estado atual: **v25** (02/10/2026), publicado no GitHub. Foco: bateria.

---

## 1. Como rodar

```bash
cd drum-practice && source .venv/bin/activate
drum-practice                      # ou: python -m musicpractice.app; abre http://localhost:7860
git pull                           # atualizar
```

- Depois de atualizar: **Ctrl+F5** no navegador.
- Para que uma melhoria de transcrição valha numa música já analisada: **Analyze song** de novo.
- Porta 7860 ocupada: `pkill -f musicpractice.app`.
- No Windows, rodar dentro do WSL (Ubuntu): o Smart App Control do Windows bloqueava o executável
  e DLLs (pandas, numba). O navegador e o kit MIDI ficam no Windows normalmente.

Instalação do zero: seção *Install* do README.

---

## 1b. Onde cada parte roda

Todo o processamento roda no PC do usuário (WSL Ubuntu). Nenhum áudio é enviado para servidor.

| Etapa | Modelo / biblioteca | Onde roda | Internet |
|---|---|---|---|
| Download da música | yt-dlp | local | sim: baixa do YouTube/SoundCloud |
| Separação (6 faixas) | Demucs htdemucs_6s (PyTorch) | local, placa de vídeo se houver | só 1ª vez: pesos ~53 MB |
| Separação da bateria | modelo de bateria do htdemucs_ft | local, placa de vídeo se houver | só 1ª vez: pesos ~80 MB |
| Batidas de bateria | ADTOF (PyTorch) | local, processador | não (pesos vêm no pacote) |
| Notas (guitarra etc.) | Basic Pitch (ONNX) | local, processador | não (modelo vem no pacote) |
| Tempo, acordes, seções, padrões, grade | librosa, numpy, scipy (sem IA) | local | não |
| Interface | Gradio (servidor local) + navegador | local (localhost:7860) | fontes do Google; Gradio envia estatísticas anônimas de uso por padrão |

- Pesos do Demucs ficam em `~/.cache/torch/hub/checkpoints/`.
- Os "agentes" são código Python comum, não chamadas a um modelo de linguagem; o app não usa
  Claude nem outra IA de texto.
- Para cortar as estatísticas do Gradio: `export GRADIO_ANALYTICS_ENABLED=False` antes de abrir.
- Sem internet o app funciona com músicas já baixadas (upload de arquivo), depois que os pesos
  do Demucs foram baixados uma vez; só as fontes da página caem para as do sistema.

## 2. Fluxo

```
Link -> download (yt-dlp) -> separação (Demucs, 6 faixas) -> análise (andamento, compassos,
tom, acordes, seções) -> transcrição (notas/tab ou bateria) -> player na mesma página -> export
```

Tudo fica em cache por música em `~/.music-practice/<id>/artifacts.json`; cada etapa só roda
uma vez, a não ser que fique desatualizada (ver 4.3).

---

## 3. Arquitetura

```
musicpractice/
  agents.py          5 agentes (Input, Analysis, Separation, Transcription, Practice) + Conductor
  app.py             interface Gradio (uma página) + canal oculto para salvar correções de bateria
  core/              Song, Workspace (cache por música), Registry de plugins
  plugins/
    input.py         link -> WAV (yt-dlp; recusa Spotify/Apple Music etc.)
    separation.py    Demucs htdemucs_6s
    rhythm.py        batidas e compassos (librosa + heurística de tempo forte)
    harmony.py       tom (Krumhansl) e acordes (templates + Viterbi)
    structure.py     seções A/B/C (novidade na auto-similaridade)
    transcription.py notas (Basic Pitch via ONNX; TensorFlow escondido)
    tab.py           notas -> corda/casa (Viterbi sobre posições da mão)
    drums_adtof.py   bateria com ADTOF (rede neural, preferido)
    drums.py         detector básico de bateria (NMF + regras), MIDI de bateria
    drum_edit.py     sensibilidade, regra das "três mãos", edições manuais
    drum_tab.py      grade de bateria (x / o por semicolcheia)
  ui/
    player.py        HTML do player (dados da música embutidos em JSON)
    player.js        player sincronizado, mixer, layout, valores digitáveis
    highway.js       "Guitar Hero" de bateria + Web MIDI + monitor MIDI
    editor.js        painel "Fix transcription"
    player.css       estilos (tudo dentro de #mp-wrap; @keyframes no nível de cima)
    kit.py           desenho do kit visto de cima
tests/               testes Python, roteiros de navegador (Playwright), benchmark MDB
```

Os "agentes" são Python comum, não chamadas a LLM: cada etapa tem um procedimento certo, e um
modelo de linguagem só traria custo e aleatoriedade. Nova funcionalidade = um plugin que declara
o que precisa (`requires`) e o que produz (`produces`); o Registry resolve a ordem e o cache.

---

## 4. Decisões importantes (e por quê)

### 4.1 Bateria: ADTOF em vez do detector próprio
Medido no **MDB Drums** (23 gravações reais, ~8.000 batidas anotadas à mão), F1:

| Detector | Bumbo | Caixa | Chimbal | Pratos | Tons |
|---|---|---|---|---|---|
| ADTOF, gravação de bateria limpa (melhor caso) | 0,96 | 0,80 | 0,86 | 0,87 | 0,53 |
| ADTOF, bateria separada pelo Demucs (o que o app faz) | 0,92 | 0,77 | 0,86 | 0,84 | 0,45 |
| ADTOF, mix completo | 0,85 | 0,70 | 0,85 | 0,83 | 0,47 |
| Detector básico, faixa de bateria | 0,93 | 0,59 | 0,70 | 0,10 | 0,02 |

- Tons com limiar 0,5 (o padrão do ADTOF, 0,32, acerta só 1 em 5 tons que reporta).
- Crash x ride decidido por quanto o prato "soa" depois da batida (94% certo).
- Licença do modelo: CC BY-NC-SA 4.0 (ok para uso pessoal).

### 4.2 Transcrição usa a faixa isolada
Sempre que a separação existe. Medido com o Demucs de verdade (htdemucs_6s, mesmo do app,
separando as 23 mixes do MDB): bumbo 0,85 -> 0,92, caixa 0,70 -> 0,77, chimbal 0,85 -> 0,86,
pratos 0,84 -> 0,84, tons 0,47 -> 0,45. (Os 0,96/0,80 de antes eram com a gravação de bateria
limpa original, o melhor caso possível.)

Pós-processamento testado na faixa separada (02/10/2026), F1 nas 23 músicas:
| Variante | Bumbo | Caixa | Chimbal | Pratos | Tons |
|---|---|---|---|---|---|
| faixa separada (app hoje) | 0,922 | 0,766 | 0,860 | 0,839 | 0,453 |
| Demucs com 2 deslocamentos | 0,923 | 0,766 | 0,862 | 0,840 | 0,453 |
| faixa + noise gate | 0,930 | 0,753 | 0,850 | 0,523 | 0,438 |
| média faixa+mix (chimbal/pratos/tons) | 0,922 | 0,766 | 0,873 | 0,845 | 0,488 |
| **htdemucs_ft (modelo de bateria)** | **0,931** | **0,773** | 0,857 | **0,855** | **0,462** |
| gravação limpa (melhor caso) | 0,961 | 0,797 | 0,862 | 0,871 | 0,531 |
Conclusão: 2 deslocamentos não ajuda (dobra o tempo); gate destrói pratos; a média com a mix
ganha na soma mas é instável (8 músicas melhores, 6 piores, até -0,21), contra o objetivo de
consistência. Nada entrou no app. O espaço que sobra (bumbo 0,92 -> 0,96, caixa 0,77 -> 0,80)
é da separação. Testado o htdemucs_ft (só o modelo de bateria dele, f7e0c4bc): melhor em
11 músicas, pior em 4 (Country1 -0,13, a mesma que oscila em todas as variantes). ENTROU (v24):
o app separa tudo com htdemucs_6s e refaz só a bateria com o modelo de bateria do htdemucs_ft
(~10 s a mais por música com placa de vídeo). Músicas antigas trocam a faixa e retranscrevem a
bateria uma vez, sozinhas; edições à mão são mantidas.

### 4.2b Grade de compassos até o fim
O detector de tempo (librosa) descarta as batidas fracas do fim: num fade de 12 s ele parou
~10 s antes do fim. Sem compasso, as notas do fim não tinham onde ser desenhadas. Agora a grade
continua no mesmo espaçamento até onde o som acaba (50 dB abaixo do pico). Músicas antigas
refazem a grade (e acordes/seções) sozinhas no próximo "Analyze song".

### 4.2c Mesma batida, mesmo desenho (grade guiada pela bateria)
Teste de repetibilidade (14 gravações do MDB, mesma música "subida de novo" com 30 ms de atraso
e 3 dB a menos, ou em MP3): a IA ouve as mesmas batidas em 97 a 99% dos casos; o que mudava era
o DESENHO, por causa da grade de tempo:
- batidas do detector ~40 ms atrasadas (todas as músicas): batidas no tempo caíam perto da borda
  entre duas casas e "pulavam" de casa;
- linha do compasso no tempo errado em 4 de 14 músicas; metade do andamento em 1 de 14.
Correção (plugins/drum_grid.py): depois da bateria transcrita, a grade é ajustada pelo próprio
baterista: suaviza as batidas, alinha pelo meio das batidas de bumbo/caixa/chimbal próximas,
dobra o andamento se a caixa cai em todo tempo, e escolhe o tempo 1 por bumbo no 1 e 3 e caixa
no 2 e 4 (acordes desempatam). Resultado contra a grade feita das anotações à mão: 0,50 -> 0,71
de concordância; linhas de compasso certas 65% -> 88%; mesma música re-subida 90% -> 91%.
Também: o Demucs embaralhava o áudio com um deslocamento ALEATÓRIO a cada execução; agora com
semente fixa, a mesma música dá as mesmas faixas.
Testado e descartado: re-rotular batidas pelo timbre (mesmo som = mesma peça) não mudou nada no
MDB (o modelo já é consistente dentro da música).
Não resolvido: músicas em que o detector erra feio o andamento (1 de 14), suingue/tercina.

### 4.2d Bumbo ouvido duas vezes (caso real: Audioslave, "Gasoline")
Introdução = um bumbo repetido, mas alguns saíam também como caixa ou chimbal fracos (logo acima
do limiar). O som dessas batidas era idêntico ao dos bumbos "sozinhos" (±2 dB em todas as
faixas de frequência). Regra (plugins/drum_echo.py): caixa/chimbal fraco (< 2x o limiar) em cima
de um bumbo, que não tem mais energia na sua faixa (caixa 2-6 kHz, chimbal > 6 kHz) do que os
bumbos sozinhos vizinhos, é eco e sai. Na música: as 6 marcas falsas da introdução somem
(31 na música toda). No MDB é neutra (chimbal 0,862 -> 0,869; caixa 0,797 -> 0,796); aplicada a
pratos e tons piorou, então vale só para caixa e chimbal.

### 4.3 Cache que se corrige sozinho
Músicas transcritas antes do Demucs (a partir do mix), com o detector antigo, ou antes de
existirem os "candidatos" (ver 4.4) são refeitas automaticamente no próximo "Analyze song".

### 4.4 Sensibilidade por família (sliders)
O detector guarda todas as batidas com uma nota de confiança ("candidatos"); os sliders só
filtram, sem rodar o modelo de novo. Conferido: filtrar no padrão dá exatamente a saída
original do ADTOF (0 diferenças em 7.716 batidas).
**Expectativa honesta:** escolher a melhor sensibilidade por música melhora o F1 em ~0,01 na
mediana, e 0,1 a 0,3 em poucas músicas.

### 4.5 O que realmente falta (MDB Drums)
| Tipo | Encontradas |
|---|---|
| Caixa normal | ~96% |
| Bumbo | ~97% |
| Ghost notes da caixa | 39% (56% na sensibilidade máxima, e metade vira "tom") |
| Chimbal de pé | 44% |
| Vassourinha | 61% |

Conclusão: o que falta é nuance; o caminho é edição manual (de preferência copiando a correção
para as repetições da seção).

### 4.6 Regra das "três mãos"
Se 3 ou mais peças de mão caem no mesmo instante (30 ms), sai a batida mais fraca. O chimbal
fica (pode ser o pedal), exceto quando um ride ou crash soa junto: aí é o mesmo prato ouvido
duas vezes e sai o mais fraco dos dois (no MDB isso acertou 13 de 16). No MDB, 16 de 16
grupos de "três mãos" eram erros. O painel mostra quantas
foram removidas; dá para devolver à mão.

### 4.7 Padrões e passos (v19)
**Padrões.** Cada compasso vira marcas (peça, semicolcheia) com peso: bumbo e caixa 1, tons
0,7, chimbal e ride 0,5 (é onde o baterista varia e a IA erra mais); o crash é ignorado.
Distância = Jaccard ponderado; agrupamento hierárquico (média) cortado em 0,45.
Medido no MDB Drums (680 compassos, batidas das anotações), concordância entre o agrupamento
feito da transcrição e o feito das anotações à mão (índice de Rand ajustado, 1 = igual):

| Regra | Faixa de bateria | Mix |
|---|---|---|
| v18 (igualdade com 12% de tolerância) | 0,47 | 0,25 |
| v19 (pesos + agrupamento) | 0,73 | 0,70 |

**Passos.** Frase = um groove repetido + a virada que fecha (nova frase quando muda o groove,
ou depois de uma virada se a frase já tem 4+ compassos). A mesma frase tocada de novo em seguida
é o mesmo passo repetido: "(Groove 2 ×3 + fill) ×6" é 1 passo. MDB: 111 passos da transcrição
contra 111 das anotações (≈ 1 passo a cada 6 compassos); rock/pop de 16 a 20 compassos dá 1 a 3
passos, jazz improvisado continua fragmentado (como deve).
Limite: se a IA confunde chimbal e ride num trecho, um groove de chimbal e um de ride podem
virar o mesmo padrão.

**Na tela.** Faixa de padrões com chips e quadradinhos por compasso (v18) + modo **Steps**:
mostra um compasso só (o groove do passo, desenhado uma vez); a cada repetição o playhead volta
ao início do MESMO compasso com um "varrido" suave, o contador sobe ("2 de 3"), bolinhas da frase
se preenchem, "Passada 1 de 8", lista de passos com ✓ nos concluídos e aviso do próximo.

### 4.8 Testado e descartado
- **Chimbal aberto x fechado pelo som:** não generaliza entre músicas (a 70% de precisão, quase
  nenhum aberto encontrado). Não entrou no app.
- **Outras percussões (pandeirola, cowbell, shaker):** o modelo só conhece 5 famílias; das 32
  pandeirolas do MDB, 29 viraram "chimbal". Modelos maiores (YourMT3+, Omnizart) não foram
  validados. Além disso, a TDX-N1 não tem pads para essas peças: elas ficam no áudio de
  acompanhamento.

### 4.9 Player
- Página única: o áudio toca no navegador; Python só monta o HTML com os dados.
- Mixer: cada faixa vira uma cópia Opus pequena, baixada inteira para a memória (o Chrome limita
  ~6 conexões por servidor); uma faixa comanda o tempo e as outras seguem ajustando a velocidade.
  Diferença medida entre faixas: normalmente < 10 ms.
- Loops e saltos começam 40 ms antes do compasso, para não cortar o ataque.
- Playhead: segue os tempos e passa por cada marca no momento em que ela soa (corrigia meia
  semicolcheia de atraso; na bateria também compensa a detecção de tempo, que costuma ficar
  20 a 40 ms atrasada).
- Grade: uma pauta contínua por linha, nomes das peças só no início, divisões discretas.
- Altura da partitura: alça embaixo dela; arrastar para baixo mostra mais linhas à frente (1 a 8,
  sempre linhas inteiras), setas ajustam, duplo clique volta a 2; fica salvo no navegador.
- Follow: a página desliza para a próxima linha durante o último tempo da linha (com
  aceleração e freada suaves), calculado pela posição da música a cada quadro; a nova linha
  chega ao topo exatamente quando o primeiro compasso dela começa. Rolar com o mouse pausa o
  follow por 2,5 s. Medido: ~27 quadros de deslize, maior passo 7 px, 60 quadros/s.

### 4.10 Highway (bateria eletrônica via MIDI)
- Web MIDI no Chrome do Windows (nada a configurar no Ubuntu).
- Julgamento em tempo real: Perfect 30 ms, Good 60 ms, OK 100 ms.
- Calibração de latência (8 cliques), "learn" para mapear pads, monitor MIDI (nota e
  velocidade de cada pad, posição do pedal do chimbal em CC4).
- Tons começam sem pontuar (a transcrição erra muitos).

---

## 5. Equipamento

- Bateria eletrônica **Aroma TDX-N1**: caixa 8" mesh, tons 6", bumbo, chimbal 9" com pedal
  controlador, crash e ride 9" de duas zonas, USB/Bluetooth MIDI.
- O mapa de notas MIDI do TDX-N1 **não foi encontrado publicado**: precisa ser lido no monitor.

---

## 6. To do

1. Com a TDX-N1 conectada, anotar no monitor do Highway ("Last pad:") a nota de cada pad e zona:
   caixa centro e aro; chimbal fechado, aberto e só pedal (e a faixa do CC4); crash borda e
   meio; ride meio e cúpula; cada tom; bumbo.
2. Com esses números: linhas extras no "Fix transcription" só para zonas que o kit toca
   (chimbal aberto, chimbal de pé, cúpula do ride, aro se existir), pistas no Highway, e mapa
   MIDI do export igual ao do módulo.
3. Copiar um compasso corrigido para todas as repetições da mesma seção.
4. Botão "×2 / ÷2 andamento" para músicas detectadas com metade ou o dobro do tempo
   (aconteceu: uma música saiu com 43 BPM e caixa nos 4 tempos).
5. Depois da bateria: precisão de guitarra e baixo.

Ideias mais distantes: detecção de compasso com modelo (`beat_this`) para músicas com mudança
de andamento; acordes com sétimas; export para Guitar Pro / MusicXML; registro de progresso por
seção; coach com LLM.

---

## 7. Limites conhecidos

- Tablatura é rascunho (bends, slides, hammer-ons não são detectados).
- Acordes só maiores e menores.
- Compassos assumem 4/4; músicas com mudança de andamento derrapam.
- Nomes de seção (Chorus, Solo) são palpites; as letras (A/B) são mais confiáveis.
- Duas guitarras não se separam uma da outra.
- Bateria: tons e ghost notes são o ponto fraco; aberto x fechado do chimbal não é distinguido.

---

## 8. Testes

```bash
python -m pytest -q                                 # 42 testes, ~3 min, músicas sintéticas
python tests/browser_check.py      /tmp/out         # player (guitarra)
python tests/browser_check_drums.py /tmp/out        # kit, grade, cores, pauta contínua
python tests/browser_check_mixer.py /tmp/out        # mixer e sincronia das faixas
python tests/browser_check_highway.py /tmp/out      # Highway com kit MIDI simulado
python tests/browser_check_fix.py  /tmp/out         # painel de correção, playhead, follow, fluidez
python tests/benchmark_mdb.py drum_only             # precisão real (precisa do MDB Drums, 3 GB)
```

---

## 9. Histórico de versões (resumo)

| Versão | O que entrou |
|---|---|
| v1 a v3 | App base: link, separação, análise, transcrição, tab, prática, export; rodar no WSL |
| v4 | Player numa página só, sincronizado com a música |
| v5 a v7 | Bateria: croqui do kit, cores iguais na grade, ADTOF, play/pause corrigido |
| v8 | Tablatura maior em duas linhas; um player por vez; animações que piscam a peça inteira |
| v9 | Layout ajustável (kit/grade), mixer por instrumento ao vivo |
| v10 | Highway estilo Guitar Hero com bateria eletrônica MIDI |
| v11 | Transcrição refeita a partir da faixa isolada quando ela passa a existir |
| v12 | Painel "Fix transcription": sensibilidade por peça e edição na grade |
| v13 | Playhead sincronizado com as notas; valores digitáveis nos sliders |
| v14 | Pauta contínua por linha (nomes só no início) |
| v15 | Monitor MIDI no Highway |
| v16 | Regra das "três mãos"; to do no README |
| v17 | Follow com deslize suave sincronizado; transições mais leves no destaque e no playhead |
| v18 | Padrões repetidos (grooves, viradas, únicos) com cores, faixa da música e loop por padrão; regra das três mãos trata prato duplicado |
| v19 | Padrões mais tolerantes (pesos + agrupamento) e modo Steps: a música em poucos passos que se repetem |
| v20 | Alça para escolher quantas linhas de partitura ver à frente |
| v25 | Página inteira no visual do player (tema escuro único, amarelo de destaque), título e números da música no topo do player, campos desnecessários removidos (instrumento, caixas de seleção, áudios duplicados, explicações) |
| v24 | Bateria separada com o modelo de bateria do htdemucs_ft (melhor que o 6s em bumbo, caixa, pratos e tons) |
| v23 | Bumbo ouvido duas vezes vira um só (caixa/chimbal 'eco' do bumbo) |
| v22 | Grade guiada pela bateria (alinhamento, tempo 1, andamento dobrado) e Demucs repetível |
| v21 | Alça colada na partitura; grade de compassos vai até o fim da música (antes o detector de tempo parava antes, em fade-outs e finais calmos, e o fim ficava sem transcrição) |
