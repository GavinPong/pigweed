// Copyright 2020 The Pigweed Authors
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
// License for the specific language governing permissions and limitations under
// the License.

#include "pw_protobuf/decoder.h"

#include "gtest/gtest.h"
#include "pw_preprocessor/util.h"

namespace pw::protobuf {
namespace {

class TestDecodeHandler : public DecodeHandler {
 public:
  Status ProcessField(Decoder* decoder, uint32_t field_number) override {
    std::string_view str;

    switch (field_number) {
      case 1:
        decoder->ReadInt32(field_number, &test_int32);
        break;
      case 2:
        decoder->ReadSint32(field_number, &test_sint32);
        break;
      case 3:
        decoder->ReadBool(field_number, &test_bool);
        break;
      case 4:
        decoder->ReadDouble(field_number, &test_double);
        break;
      case 5:
        decoder->ReadFixed32(field_number, &test_fixed32);
        break;
      case 6:
        decoder->ReadString(field_number, &str);
        std::memcpy(test_string, str.data(), str.size());
        test_string[str.size()] = '\0';
        break;
    }

    called = true;
    return Status::OK;
  }

  bool called = false;
  int32_t test_int32 = 0;
  int32_t test_sint32 = 0;
  bool test_bool = true;
  double test_double = 0;
  uint32_t test_fixed32 = 0;
  char test_string[16];
};

TEST(Decoder, Decode) {
  Decoder decoder;
  TestDecodeHandler handler;

  // clang-format off
  uint8_t encoded_proto[] = {
    // type=int32, k=1, v=42
    0x08, 0x2a,
    // type=sint32, k=2, v=-13
    0x10, 0x19,
    // type=bool, k=3, v=false
    0x18, 0x00,
    // type=double, k=4, v=3.14159
    0x21, 0x6e, 0x86, 0x1b, 0xf0, 0xf9, 0x21, 0x09, 0x40,
    // type=fixed32, k=5, v=0xdeadbeef
    0x2d, 0xef, 0xbe, 0xad, 0xde,
    // type=string, k=6, v="Hello world"
    0x32, 0x0b, 'H', 'e', 'l', 'l', 'o', ' ', 'w', 'o', 'r', 'l', 'd',
  };
  // clang-format on

  decoder.set_handler(&handler);
  EXPECT_EQ(decoder.Decode(as_bytes(span(encoded_proto))), Status::OK);
  EXPECT_TRUE(handler.called);
  EXPECT_EQ(handler.test_int32, 42);
  EXPECT_EQ(handler.test_sint32, -13);
  EXPECT_FALSE(handler.test_bool);
  EXPECT_EQ(handler.test_double, 3.14159);
  EXPECT_EQ(handler.test_fixed32, 0xdeadbeef);
  EXPECT_STREQ(handler.test_string, "Hello world");
}

TEST(Decoder, Decode_OverridesDuplicateFields) {
  Decoder decoder;
  TestDecodeHandler handler;

  // clang-format off
  uint8_t encoded_proto[] = {
    // type=int32, k=1, v=42
    0x08, 0x2a,
    // type=int32, k=1, v=43
    0x08, 0x2b,
    // type=int32, k=1, v=44
    0x08, 0x2c,
  };
  // clang-format on

  decoder.set_handler(&handler);
  EXPECT_EQ(decoder.Decode(as_bytes(span(encoded_proto))), Status::OK);
  EXPECT_TRUE(handler.called);
  EXPECT_EQ(handler.test_int32, 44);
}

TEST(Decoder, Decode_Empty) {
  Decoder decoder;
  TestDecodeHandler handler;

  decoder.set_handler(&handler);
  EXPECT_EQ(decoder.Decode(span<std::byte>()), Status::OK);
  EXPECT_FALSE(handler.called);
  EXPECT_EQ(handler.test_int32, 0);
  EXPECT_EQ(handler.test_sint32, 0);
}

TEST(Decoder, Decode_BadData) {
  Decoder decoder;
  TestDecodeHandler handler;

  // Field key without a value.
  uint8_t encoded_proto[] = {0x08};

  decoder.set_handler(&handler);
  EXPECT_EQ(decoder.Decode(as_bytes(span(encoded_proto))), Status::DATA_LOSS);
}

// Only processes fields numbered 1 or 3.
class OneThreeDecodeHandler : public DecodeHandler {
 public:
  Status ProcessField(Decoder* decoder, uint32_t field_number) override {
    switch (field_number) {
      case 1:
        EXPECT_EQ(decoder->ReadInt32(field_number, &field_one), Status::OK);
        break;
      case 3:
        EXPECT_EQ(decoder->ReadInt32(field_number, &field_three), Status::OK);
        break;
      default:
        // Do nothing.
        break;
    }

    called = true;
    return Status::OK;
  }

  bool called = false;
  int32_t field_one = 0;
  int32_t field_three = 0;
};

TEST(Decoder, Decode_SkipsUnprocessedFields) {
  Decoder decoder;
  OneThreeDecodeHandler handler;

  // clang-format off
  uint8_t encoded_proto[] = {
    // type=int32, k=1, v=42
    // Should be read.
    0x08, 0x2a,
    // type=sint32, k=2, v=-13
    // Should be ignored.
    0x10, 0x19,
    // type=int32, k=2, v=3
    // Should be ignored.
    0x10, 0x03,
    // type=int32, k=3, v=99
    // Should be read.
    0x18, 0x63,
    // type=int32, k=4, v=16
    // Should be ignored.
    0x20, 0x10,
  };
  // clang-format on

  decoder.set_handler(&handler);
  EXPECT_EQ(decoder.Decode(as_bytes(span(encoded_proto))), Status::OK);
  EXPECT_TRUE(handler.called);
  EXPECT_EQ(handler.field_one, 42);
  EXPECT_EQ(handler.field_three, 99);
}

// Only processes fields numbered 1 or 3.
class ExitOnOneDecoder : public DecodeHandler {
 public:
  Status ProcessField(Decoder* decoder, uint32_t field_number) override {
    switch (field_number) {
      case 1:
        EXPECT_EQ(decoder->ReadInt32(field_number, &field_one), Status::OK);
        return Status::CANCELLED;
      case 3:
        EXPECT_EQ(decoder->ReadInt32(field_number, &field_three), Status::OK);
        break;
      default:
        // Do nothing.
        break;
    }

    return Status::OK;
  }

  int32_t field_one = 0;
  int32_t field_three = 1111;
};

TEST(Decoder, Decode_StopsOnNonOkStatus) {
  Decoder decoder;
  ExitOnOneDecoder handler;

  // clang-format off
  uint8_t encoded_proto[] = {
    // type=int32, k=1, v=42
    // Should be read.
    0x08, 0x2a,
    // type=int32, k=3, v=99
    // Should be skipped.
    0x18, 0x63,
    // type=int32, k=2, v=16
    // Should be skipped.
    0x08, 0x10,
  };
  // clang-format on

  decoder.set_handler(&handler);
  EXPECT_EQ(decoder.Decode(as_bytes(span(encoded_proto))), Status::CANCELLED);
  EXPECT_EQ(handler.field_one, 42);
  EXPECT_EQ(handler.field_three, 1111);
}

}  // namespace
}  // namespace pw::protobuf
