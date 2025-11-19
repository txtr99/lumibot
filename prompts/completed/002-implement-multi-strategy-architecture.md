<objective>
Implement a complete multi-strategy trading architecture for LumiBot that enables running 30+ independent trading strategies within a single Python process, achieving 95%+ API call reduction while maintaining complete strategy independence through virtual position tracking and shared resource optimization.

This implementation will transform LumiBot's single-strategy architecture into a production-ready multi-strategy system based on comprehensive research findings documented across 6 analysis documents (214 KB total).

**Why this matters:** This enables efficient scaling from 1 strategy to 30+ concurrent strategies while respecting API rate limits, minimizing redundant data fetches, and maintaining accurate independent position tracking for each strategy - critical for professional trading operations.

**Who will use this:** Trading system operators managing multiple algorithmic strategies across futures markets (ES, NQ, YM, etc.) who need to maximize strategy diversity while minimizing API overhead and maintaining accurate per-strategy attribution.

**What it will accomplish:** A complete implementation of Design 2 (Enhanced Multi-Strategy Executor) from the research, including all core components, comprehensive testing, working examples, and production deployment readiness.
</objective>

<context>
This task builds upon completed research investigation that produced:

1. **lumibot_architecture_analysis.md** (29 KB) - Deep analysis of LumiBot's 5-layer architecture
2. **strategy_implementation_patterns.md** (39 KB) - 10 reusable patterns from existing strategies
3. **multi_strategy_design_proposals.md** (48 KB) - 3 architectural designs with recommendation
4. **performance_scalability_research.md** (33 KB) - Industry best practices validation
5. **master_strategy_architecture.md** (50 KB) - Complete implementation specifications
6. **RESEARCH_SUMMARY.md** (15 KB) - Executive overview and verification

**Research Findings Summary:**
- LumiBot architecture already supports multi-strategy extension
- Virtual position tracking provides complete strategy isolation
- Event-driven execution model works for concurrent strategies
- Main gaps: No shared data infrastructure, no global rate limiting, no attribution framework
- Design 2 (Enhanced Multi-Strategy Executor) recommended for 30-strategy target

**Key Technical Constraints:**
- 10+ independent strategies per file, 30 total across 3 markets
- Same symbol, same direction trading per file
- 2-second delays between order creation (rate limiting)
- One contract per strategy, independent entry/exit decisions
- Must work correctly for both backtesting and live trading
- Independent virtual position containers for each strategy
- Shared data sources (single DataFrame download), calendar, trading hours

**Files to examine:**
@lumibot/strategies/strategy.py - Base strategy class
@lumibot/strategies/strategy_executor.py - Current executor implementation
@lumibot/tools/virtual_position_tracker.py - Virtual position management
@lumibot/tools/trading_calendar.py - Trading calendar system
@strategies/test_strat_v02_working_with_virtual_positions.py - Reference implementation
</context>

<requirements>
**Phase 1: Core Infrastructure Components (Week 1-2)**

1. **SharedDataManager** - Minimize API calls through intelligent caching
   - Fetch market data once for all strategies
   - 60-second TTL cache invalidation
   - Thread-safe access with proper locking
   - Support both Pandas and Polars DataFrames
   - Cache key format: `{symbol}_{length}_{timestep}`
   - Target: 95%+ cache hit rate, 1 API call vs. 30 calls per iteration

2. **GlobalRateLimiter** - Enforce 2-second delays across all strategies
   - Thread-safe timing coordination
   - Blocking wait for sequential execution
   - Non-blocking status checks
   - Accurate delay calculation accounting for execution time
   - Token bucket or fixed delay algorithm

3. **StrategyState** - Independent state containers
   - One per strategy instance
   - Independent VirtualPositionTracker
   - Isolated pending orders, P&L tracking
   - Trade history for attribution
   - No shared mutable state between strategies

4. **StrategyAttribution** - Performance tracking
   - Per-strategy P&L calculation
   - Win rate, Sharpe ratio, max drawdown
   - Trade count and average P&L
   - Report generation (DataFrame output)
   - Identify best/worst performers

**Phase 2: Multi-Strategy Executor (Week 2-3)**

5. **MultiStrategyExecutor** - Main coordination class
   - Manages list of StrategyState objects
   - Shares broker, data source, calendar instances
   - Orchestrates execution loop across all strategies
   - Handles errors gracefully per strategy
   - Maintains backward compatibility with LumiBot framework

6. **Execution Flow Implementation**
   - Fetch data once for all unique symbols
   - Distribute cached data to each strategy
   - Independent signal generation per strategy
   - Collect pending orders from all strategies
   - Sequential order execution with rate limiting
   - Immediate virtual position updates
   - Attribution recording for completed trades

**Phase 3: Configuration and Examples (Week 3-4)**

7. **Strategy Configuration System**
   - JSON/Python dict-based config format
   - Support for strategy variants (different parameters)
   - Easy 10-strategy file creation
   - Parameter validation

8. **Working Examples**
   - 2-strategy proof-of-concept demonstrating independence
   - 10-strategy file showing scalability
   - Comprehensive test cases validating isolation

**Phase 4: Backtesting Integration (Week 4-5)**

9. **Multi-Strategy Backtest Support**
   - Bar-by-bar execution with shared timestamps
   - Realistic order sequencing with delays
   - Independent and aggregate results
   - Performance attribution report generation

**Phase 5: Production Readiness (Week 5-6)**

10. **Error Handling and Resilience**
    - Order rejection handling per strategy
    - Data fetch failure recovery
    - Strategy failure isolation (don't crash all)
    - Comprehensive logging per strategy

11. **Monitoring and Logging**
    - Per-strategy log streams with IDs
    - Aggregate performance summaries
    - Real-time execution tracking
    - Performance dashboard data

12. **Testing and Validation**
    - Unit tests for all components
    - Integration tests for multi-strategy scenarios
    - Backtest validation framework
    - Performance benchmarking
</requirements>

<implementation>
**Development Approach:**
Follow Test-Driven Development (TDD) practices with the red-green-refactor cycle:
1. Write tests first defining expected behavior
2. Implement minimal code to pass tests
3. Refactor for clarity and performance

**Code Organization:**
Create new files in appropriate locations:
- `./lumibot/strategies/multi_strategy_executor.py` - Main executor class
- `./lumibot/tools/shared_data_manager.py` - Data caching component
- `./lumibot/tools/global_rate_limiter.py` - Rate limiting component
- `./lumibot/tools/strategy_attribution.py` - Performance attribution
- `./strategies/multi_strategy_template.py` - Template for multi-strategy files
- `./strategies/sample_two_strategy.py` - Working 2-strategy example
- `./tests/test_multi_strategy.py` - Comprehensive test suite

**Implementation Priorities:**

**MUST HAVE (Critical for 30-strategy operation):**
- SharedDataManager with 60s TTL caching
- GlobalRateLimiter with 2-second delays
- StrategyState with independent VirtualPositionTracker
- MultiStrategyExecutor with execution loop
- Basic error isolation per strategy

**SHOULD HAVE (Important for production):**
- StrategyAttribution with P&L tracking
- Comprehensive logging per strategy
- Multi-strategy backtest support
- Configuration validation

**NICE TO HAVE (Future enhancements):**
- Real-time dashboard integration
- Dynamic strategy enable/disable
- Advanced signal aggregation
- Portfolio-level risk limits

**Code Quality Standards:**
- Follow existing LumiBot coding conventions
- Use type hints for all public methods
- Comprehensive docstrings with examples
- PEP 8 compliance
- Thread-safety where needed (use threading.Lock)

**WHY these constraints matter:**
- 2-second delays: Prevents broker API rate limit violations that would halt all trading
- Independent positions: Enables strategies to hold opposite positions in same symbol without conflicts
- Shared data caching: Reduces API calls from 1800/hour to <60/hour (95%+ reduction)
- Virtual position tracking: Unreliable broker position APIs cannot be trusted for trading logic
- Bar-by-bar backtesting: Prevents look-ahead bias that would invalidate backtest results
</implementation>

<research_context>
Before implementation, thoroughly read and analyze ALL research documents:

1. Read `./research/lumibot_architecture_analysis.md` to understand:
   - Current LumiBot architecture and extension points
   - StrategyExecutor lifecycle and threading model
   - VirtualPositionTracker implementation
   - Broker and DataSource abstractions
   - TradingCalendar integration patterns

2. Read `./research/strategy_implementation_patterns.md` to extract:
   - Pattern 1: Independent state management via self.vars
   - Pattern 3: Virtual position tracking usage
   - Pattern 4: Order execution with virtual updates
   - Pattern 5: Parameter-based configuration
   - Anti-patterns to avoid (shared mutable state, blocking waits, redundant API calls)

3. Read `./research/multi_strategy_design_proposals.md` to understand:
   - Design 2 (Enhanced Multi-Strategy Executor) architecture
   - Component diagram and data flow
   - Performance targets (30 strategies, <10 MB memory, 95%+ API reduction)
   - Implementation timeline and phases

4. Read `./implementation/master_strategy_architecture.md` for:
   - Complete component specifications with interfaces
   - Execution flow algorithms (on_trading_iteration)
   - Signal generation patterns
   - Order execution with rate limiting
   - Configuration format examples
   - Error handling strategies
   - Testing strategy and benchmarks

5. Reference `./RESEARCH_SUMMARY.md` for:
   - Verification criteria (all must be met)
   - Performance targets and metrics
   - Implementation readiness checklist
</research_context>

<execution_strategy>
**Recommended Implementation Sequence:**

**Week 1-2: Foundation**
1. Read and deeply understand all research documents (critical!)
2. Implement SharedDataManager with comprehensive unit tests
3. Implement GlobalRateLimiter with timing validation tests
4. Implement StrategyState dataclass with VirtualPositionTracker integration
5. Create basic MultiStrategyExecutor shell
6. Validate 2-strategy independence test passes

**Week 3-4: Enhancement**
7. Implement StrategyAttribution with metrics calculations
8. Add comprehensive per-strategy logging
9. Create 10-strategy configuration template
10. Implement multi-strategy backtest integration
11. Build working examples (2-strategy and 10-strategy)

**Week 5-6: Production Readiness**
12. Comprehensive error handling and recovery
13. Performance benchmarking against targets
14. Integration testing with 30-strategy scenarios
15. Documentation and deployment guides
16. Final validation of all verification criteria

**Critical Success Factors:**
- Strategy independence verifiable through backtests (opposite positions in same symbol)
- API call reduction measurable (instrument with logging/metrics)
- Performance acceptable for 30 strategies (<10 MB memory, <60s execution time)
- All existing LumiBot functionality maintained (backward compatibility)

**For maximum efficiency:**
- Whenever you need to perform multiple independent operations (reading research docs, running tests, implementing components), invoke all relevant tools simultaneously in parallel rather than sequentially
- After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding
</execution_strategy>

<output>
Create all implementation files with complete, production-ready code:

**Core Components:**
- `./lumibot/strategies/multi_strategy_executor.py` - Complete MultiStrategyExecutor class
- `./lumibot/tools/shared_data_manager.py` - SharedDataManager with caching
- `./lumibot/tools/global_rate_limiter.py` - GlobalRateLimiter with thread-safety
- `./lumibot/tools/strategy_attribution.py` - StrategyAttribution with metrics

**Strategy Templates and Examples:**
- `./strategies/multi_strategy_template.py` - Template for creating 10-strategy files
- `./strategies/sample_two_strategy.py` - Working 2-strategy example demonstrating independence
- `./strategies/sample_ten_strategy.py` - Working 10-strategy example showing scalability

**Testing Framework:**
- `./tests/test_shared_data_manager.py` - Unit tests for caching component
- `./tests/test_global_rate_limiter.py` - Unit tests for rate limiting
- `./tests/test_strategy_state.py` - Unit tests for state containers
- `./tests/test_multi_strategy_executor.py` - Integration tests for full system
- `./tests/test_strategy_independence.py` - Validation tests for isolation

**Configuration and Documentation:**
- `./config/multi_strategy_example.py` - Example configuration for 30 strategies
- `./docs/MULTI_STRATEGY_GUIDE.md` - Complete user guide
- `./docs/IMPLEMENTATION_NOTES.md` - Technical implementation details

All files should include:
- Comprehensive docstrings with examples
- Type hints for all parameters and return values
- Inline comments explaining complex logic
- Error handling with descriptive messages
- Logging at appropriate levels
</output>

<verification>
Before declaring implementation complete, verify ALL criteria:

**Architecture Completeness:**
- [ ] All 4 core components implemented (SharedDataManager, GlobalRateLimiter, StrategyState, StrategyAttribution)
- [ ] MultiStrategyExecutor coordinates execution correctly
- [ ] Virtual position integration maintains strategy isolation
- [ ] Rate limiting enforced globally across all strategies

**Design Validation:**
- [ ] Multi-strategy architecture supports 10+ independent strategies per file
- [ ] Shared resource optimization achieves 95%+ API call reduction (measure with logs)
- [ ] Independent position management validated (opposite positions in same symbol work)
- [ ] Same symbol, same direction constraint enforceable via configuration

**Implementation Proof:**
- [ ] Working 2-strategy example demonstrates complete independence
- [ ] Working 10-strategy example shows scalability
- [ ] Backtest results show accurate strategy independence (separate P&L)
- [ ] Rate limiting measured: actual delays match 2-second requirement
- [ ] Performance meets targets: <10 MB memory, <60s for 30 orders

**Testing Coverage:**
- [ ] Unit tests pass for all core components
- [ ] Integration tests pass for multi-strategy scenarios
- [ ] Backtest validation confirms accuracy
- [ ] Strategy independence test: Strategy1 LONG + Strategy2 SHORT = independent positions

**Production Readiness:**
- [ ] Error handling tested (order rejection, data fetch failure, strategy crash)
- [ ] Logging produces clear, debuggable output per strategy
- [ ] Configuration validation prevents invalid setups
- [ ] Documentation complete and accurate

**Performance Benchmarks:**
Run benchmarks and confirm:
- Memory usage: <10 MB for 30 strategies
- API calls: <5 data calls per minute (vs. 30+ without caching)
- Execution time: <60 seconds for 30 sequential orders (2s × 30)
- Cache hit rate: >95% (strategies use cached data)

**Regression Testing:**
- [ ] Existing single-strategy code still works
- [ ] No breaking changes to LumiBot public APIs
- [ ] Backward compatibility maintained
</verification>

<success_criteria>
Implementation is complete and successful when:

1. **Functional Requirements Met:**
   - ✅ 30 independent strategies can run in single process
   - ✅ Each strategy maintains independent virtual positions
   - ✅ Same symbol with opposite positions works correctly
   - ✅ 2-second rate limiting enforced globally
   - ✅ Shared data fetched once per iteration
   - ✅ Per-strategy P&L attribution accurate

2. **Performance Targets Achieved:**
   - ✅ Memory: <10 MB for 30 strategies
   - ✅ API efficiency: 95%+ reduction in data calls
   - ✅ Execution time: ~60s for 30 orders (acceptable)
   - ✅ Cache hit rate: >95%

3. **Quality Standards Met:**
   - ✅ All unit tests passing (100% of new code)
   - ✅ All integration tests passing
   - ✅ Code follows LumiBot conventions
   - ✅ Comprehensive documentation provided
   - ✅ Type hints and docstrings complete

4. **Production Viability Confirmed:**
   - ✅ Error handling robust (graceful degradation)
   - ✅ Logging provides clear debugging information
   - ✅ Backtest accuracy validated
   - ✅ Live trading compatible (thread-safe)
   - ✅ Monitoring/attribution functional

5. **Examples Demonstrate Capability:**
   - ✅ 2-strategy example: Shows independence
   - ✅ 10-strategy example: Shows scalability
   - ✅ 30-strategy config: Shows production readiness
   - ✅ Backtest results: Validates accuracy
</success_criteria>

<evaluation_criteria>
**Research Quality (Input to Implementation):**
- Demonstrates thorough understanding of all 6 research documents
- Correctly applies architecture patterns from analysis
- Avoids anti-patterns identified in research
- Implements recommended Design 2 architecture
- References specific research findings in code comments

**Technical Excellence:**
- Efficient resource utilization (memory, CPU, API calls)
- Thread-safe where concurrency exists
- Clean separation of concerns
- Robust error handling
- Comprehensive logging and monitoring

**Practical Viability:**
- Works correctly for both backtesting and live trading
- Performance acceptable for real-world use
- Integration maintains LumiBot compatibility
- Solution is extensible for future enhancements
- Operational procedures documented

**Innovation & Optimization:**
- Creative solutions for shared resource optimization
- Effective strategies for maintaining strategy independence
- Smart approaches to rate limiting coordination
- Scalable patterns for future growth

**Code Quality:**
- Readable and maintainable
- Well-documented with examples
- Follows Python and LumiBot conventions
- Testable architecture
- Production-ready robustness
</evaluation_criteria>
Completed: Tue 18 Nov 2025 21:39:31 MST
