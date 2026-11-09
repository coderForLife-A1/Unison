use std::{
    collections::VecDeque,
    io::{self, stdout},
    sync::mpsc::{self, Receiver, Sender},
    thread,
    time::{Duration, Instant},
};

use crossterm::{
    event::{self, Event, KeyCode, KeyEvent, KeyEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    layout::{Constraint, Direction, Layout},
    prelude::{CrosstermBackend, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Cell, List, ListItem, Paragraph, Row, Table, TableState, Wrap},
    Terminal,
};

const HISTORY_LIMIT: usize = 200;
const RULE_LIMIT: usize = 32;

#[derive(Clone, Debug)]
struct Notification {
    source: String,
    sender: String,
    body: String,
    priority: String,
}

#[derive(Clone, Debug)]
struct Rule {
    pattern: String,
    enabled: bool,
}

#[derive(Clone, Debug)]
enum AppEvent {
    Notification(Notification),
    Metrics {
        p99_ms: f64,
        suppressed: u64,
        ram_mb: f64,
    },
}

struct App {
    notifications: VecDeque<Notification>,
    rules: Vec<Rule>,
    selected_notification: usize,
    selected_rule: usize,
    editing_rule: bool,
    rule_input: String,
    status: String,
    p99_ms: f64,
    suppressed: u64,
    ram_mb: f64,
    last_tick: Instant,
}

impl App {
    fn new() -> Self {
        Self {
            notifications: VecDeque::with_capacity(HISTORY_LIMIT),
            rules: vec![
                Rule {
                    pattern: String::from(r"\b(?:otp|code)\b"),
                    enabled: true,
                },
                Rule {
                    pattern: String::from(r"\bcritical\b"),
                    enabled: true,
                },
            ],
            selected_notification: 0,
            selected_rule: 0,
            editing_rule: false,
            rule_input: String::new(),
            status: String::from("Connected | q quit | r rule editor"),
            p99_ms: 0.0,
            suppressed: 0,
            ram_mb: 0.0,
            last_tick: Instant::now(),
        }
    }

    fn apply(&mut self, event: AppEvent) {
        match event {
            AppEvent::Notification(notification) => {
                if self.notifications.len() == HISTORY_LIMIT {
                    self.notifications.pop_front();
                }
                self.notifications.push_back(notification);
                self.selected_notification = self.notifications.len().saturating_sub(1);
            }
            AppEvent::Metrics {
                p99_ms,
                suppressed,
                ram_mb,
            } => {
                self.p99_ms = p99_ms;
                self.suppressed = suppressed;
                self.ram_mb = ram_mb;
            }
        }
    }

    fn add_rule(&mut self) {
        let pattern = self.rule_input.trim().to_owned();
        if pattern.is_empty() || self.rules.len() >= RULE_LIMIT {
            self.status = String::from("Rule rejected: empty pattern or rule limit reached");
            return;
        }
        // The core engine must compile/validate the regex before persistence.
        // The TUI only edits bounded text and never executes it.
        self.rules.push(Rule {
            pattern,
            enabled: true,
        });
        self.selected_rule = self.rules.len() - 1;
        self.rule_input.clear();
        self.editing_rule = false;
        self.status = String::from("Rule staged; press s to persist through the core");
    }

    fn toggle_rule(&mut self) {
        if let Some(rule) = self.rules.get_mut(self.selected_rule) {
            rule.enabled = !rule.enabled;
            self.status = String::from("Rule changed; press s to persist through the core");
        }
    }

    fn handle_key(&mut self, key: KeyEvent) -> bool {
        if key.kind != KeyEventKind::Press {
            return false;
        }
        if self.editing_rule {
            match key.code {
                KeyCode::Esc => {
                    self.editing_rule = false;
                    self.rule_input.clear();
                }
                KeyCode::Enter => self.add_rule(),
                KeyCode::Backspace => {
                    self.rule_input.pop();
                }
                KeyCode::Char(character) if self.rule_input.len() < 512 => {
                    self.rule_input.push(character);
                }
                _ => {}
            }
            return false;
        }
        match key.code {
            KeyCode::Char('q') => return true,
            KeyCode::Char('r') => {
                self.editing_rule = true;
                self.rule_input.clear();
                self.status = String::from("Type regex; Enter add, Esc cancel");
            }
            KeyCode::Char('t') => self.toggle_rule(),
            KeyCode::Down => {
                self.selected_rule =
                    (self.selected_rule + 1).min(self.rules.len().saturating_sub(1))
            }
            KeyCode::Up => self.selected_rule = self.selected_rule.saturating_sub(1),
            _ => {}
        }
        false
    }
}

fn draw(frame: &mut ratatui::Frame, app: &App) {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(3),
        ])
        .split(frame.area());
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(70), Constraint::Percentage(30)])
        .split(vertical[1]);

    let metrics = Paragraph::new(Line::from(vec![
        Span::styled(
            " P99 ",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(format!("{:.1} ms   ", app.p99_ms)),
        Span::styled("Suppressed ", Style::default().fg(Color::Yellow)),
        Span::raw(format!("{}   ", app.suppressed)),
        Span::styled("RAM ", Style::default().fg(Color::Green)),
        Span::raw(format!("{:.1} MB", app.ram_mb)),
    ]))
    .block(Block::default().borders(Borders::ALL).title("Live metrics"));
    frame.render_widget(metrics, vertical[0]);

    let rows = app.notifications.iter().map(|notification| {
        Row::new(vec![
            Cell::from(notification.source.clone()),
            Cell::from(notification.sender.clone()),
            Cell::from(notification.priority.clone()),
            Cell::from(notification.body.chars().take(72).collect::<String>()),
        ])
    });
    let table = Table::new(
        rows,
        [
            Constraint::Length(12),
            Constraint::Length(20),
            Constraint::Length(10),
            Constraint::Min(20),
        ],
    )
    .header(
        Row::new(["Source", "Sender", "Priority", "Body"]).style(Style::default().fg(Color::Cyan)),
    )
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title("Incoming notifications"),
    )
    .row_highlight_style(Style::default().bg(Color::DarkGray));
    let mut table_state = TableState::default();
    table_state.select(Some(app.selected_notification));
    frame.render_stateful_widget(table, body[0], &mut table_state);

    let rule_items = app.rules.iter().map(|rule| {
        let marker = if rule.enabled { "[x]" } else { "[ ]" };
        ListItem::new(format!("{} {}", marker, rule.pattern))
    });
    let rules = List::new(rule_items)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Rules (t toggle, r add)"),
        )
        .highlight_style(Style::default().bg(Color::DarkGray));
    let mut rule_state = ratatui::widgets::ListState::default();
    rule_state.select(Some(app.selected_rule));
    frame.render_stateful_widget(rules, body[1], &mut rule_state);

    let footer_text = if app.editing_rule {
        format!("Regex: {}_", app.rule_input)
    } else {
        app.status.clone()
    };
    frame.render_widget(
        Paragraph::new(footer_text)
            .wrap(Wrap { trim: true })
            .block(Block::default().borders(Borders::ALL)),
        vertical[2],
    );
}

fn spawn_demo_stream(tx: Sender<AppEvent>) {
    thread::spawn(move || {
        let mut tick = 0_u64;
        loop {
            thread::sleep(Duration::from_millis(800));
            tick += 1;
            if tx
                .send(AppEvent::Notification(Notification {
                    source: if tick % 2 == 0 {
                        "Slack".into()
                    } else {
                        "Email".into()
                    },
                    sender: if tick % 2 == 0 {
                        "#platform".into()
                    } else {
                        "Calendar".into()
                    },
                    body: if tick % 3 == 0 {
                        "Review critical deployment before 16:00".into()
                    } else {
                        "Reminder: team sync starts in 15 minutes".into()
                    },
                    priority: if tick % 3 == 0 {
                        "High".into()
                    } else {
                        "Low".into()
                    },
                }))
                .is_err()
            {
                break;
            }
            if tx
                .send(AppEvent::Metrics {
                    p99_ms: 12.0 + (tick % 7) as f64,
                    suppressed: tick / 3,
                    ram_mb: 42.0 + (tick % 4) as f64 * 0.2,
                })
                .is_err()
            {
                break;
            }
        }
    });
}

fn run() -> io::Result<()> {
    enable_raw_mode()?;
    let mut output = stdout();
    execute!(output, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(output);
    let mut terminal = Terminal::new(backend)?;
    let (tx, rx): (Sender<AppEvent>, Receiver<AppEvent>) = mpsc::channel();
    spawn_demo_stream(tx);
    let mut app = App::new();

    loop {
        while let Ok(event) = rx.try_recv() {
            app.apply(event);
        }
        terminal.draw(|frame| draw(frame, &app))?;
        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if app.handle_key(key) {
                    break;
                }
            }
        }
        if app.last_tick.elapsed() >= Duration::from_secs(1) {
            app.last_tick = Instant::now();
        }
    }
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    Ok(())
}

fn main() -> io::Result<()> {
    run()
}
