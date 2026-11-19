// Copyright 2026 Mozkey contributors.
// SPDX-License-Identifier: BSD-3-Clause

#include "unix/ibus/property_handler.h"

#include <memory>

#include "client/client_mock.h"
#include "protocol/commands.pb.h"
#include "testing/gmock.h"
#include "testing/gunit.h"
#include "unix/ibus/ibus_header.h"
#include "unix/ibus/message_translator.h"

namespace mozc::ibus {
namespace {

using ::testing::_;
using ::testing::Return;

class PropertyHandlerTest : public ::testing::Test {
 protected:
  void SetUp() override {
    EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
        .WillOnce([](const commands::SessionCommand& command,
                     const commands::Context&, commands::Output*) {
          EXPECT_EQ(command.type(), commands::SessionCommand::TURN_ON_IME);
          return true;
        });
    handler_ = std::make_unique<PropertyHandler>(
        std::make_unique<NullMessageTranslator>(), true, &client_);
  }

  client::ClientMock client_;
  std::unique_ptr<PropertyHandler> handler_;
};

TEST_F(PropertyHandlerTest, DirectModeReturnsCommitAndEmptyComposition) {
  commands::Output response;
  response.mutable_status()->set_activated(false);
  response.mutable_status()->set_mode(commands::HIRAGANA);
  response.mutable_result()->set_type(commands::Result::STRING);
  response.mutable_result()->set_value("日本語");
  ASSERT_TRUE(response.IsInitialized());
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
      .WillOnce([&](const commands::SessionCommand& command,
                    const commands::Context&, commands::Output* output) {
        EXPECT_EQ(command.type(), commands::SessionCommand::TURN_OFF_IME);
        EXPECT_EQ(command.composition_mode(), commands::HIRAGANA);
        *output = response;
        return true;
      });

  const auto output =
      handler_->ProcessPropertyActivate("InputMode.Direct", PROP_STATE_CHECKED);
  ASSERT_TRUE(output.has_value());
  EXPECT_EQ(output->SerializeAsString(), response.SerializeAsString());
  EXPECT_FALSE(output->has_preedit());
  EXPECT_FALSE(output->has_candidate_window());
}

TEST_F(PropertyHandlerTest, OtherModeReturnsPreeditAndCandidates) {
  commands::Output response;
  response.mutable_status()->set_activated(true);
  response.mutable_status()->set_mode(commands::FULL_KATAKANA);
  response.mutable_preedit()->set_cursor(4);
  auto* segment = response.mutable_preedit()->add_segment();
  segment->set_annotation(commands::Preedit::Segment::UNDERLINE);
  segment->set_value("ニホンゴ");
  segment->set_value_length(4);
  auto* window = response.mutable_candidate_window();
  window->set_size(1);
  window->set_position(0);
  auto* candidate = window->add_candidate();
  candidate->set_index(0);
  candidate->set_value("日本語");
  ASSERT_TRUE(response.IsInitialized());
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
      .WillOnce([&](const commands::SessionCommand& command,
                    const commands::Context&, commands::Output* output) {
        EXPECT_EQ(command.type(),
                  commands::SessionCommand::SWITCH_COMPOSITION_MODE);
        EXPECT_EQ(command.composition_mode(), commands::FULL_KATAKANA);
        *output = response;
        return true;
      });

  const auto output = handler_->ProcessPropertyActivate("InputMode.Katakana",
                                                        PROP_STATE_CHECKED);
  ASSERT_TRUE(output.has_value());
  EXPECT_EQ(output->SerializeAsString(), response.SerializeAsString());
}

TEST_F(PropertyHandlerTest, IgnoredPropertiesDoNotProduceAnUpdate) {
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _)).Times(0);
  EXPECT_FALSE(handler_->ProcessPropertyActivate("InputMode.Direct",
                                                 PROP_STATE_UNCHECKED));
  EXPECT_FALSE(
      handler_->ProcessPropertyActivate("Unknown", PROP_STATE_CHECKED));
}

TEST_F(PropertyHandlerTest, FailedCommandDoesNotApplyPartialResponse) {
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
      .WillOnce([](const commands::SessionCommand&, const commands::Context&,
                   commands::Output* output) {
        output->mutable_status()->set_activated(false);
        output->mutable_result()->set_value("partial");
        return false;
      });
  EXPECT_FALSE(handler_->ProcessPropertyActivate("InputMode.Direct",
                                                 PROP_STATE_CHECKED));
  EXPECT_TRUE(handler_->IsActivated());
}

TEST_F(PropertyHandlerTest, MissingStatusDoesNotProduceAnUpdate) {
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _)).WillOnce(Return(true));
  EXPECT_FALSE(handler_->ProcessPropertyActivate("InputMode.Direct",
                                                 PROP_STATE_CHECKED));
  EXPECT_TRUE(handler_->IsActivated());
}

}  // namespace
}  // namespace mozc::ibus
